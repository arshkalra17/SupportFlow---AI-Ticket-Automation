import json
import os
import time
# pyrefly: ignore [missing-import]
from dotenv import load_dotenv
# pyrefly: ignore [missing-import]
from groq import Groq

# pyrefly: ignore [missing-import]
from app.tools import (
    validate_get_order_status_args,
    authorize_and_get_order_status,
    validate_create_replacement_request_args,
    create_replacement_request,
    validate_issue_refund_args,
    issue_refund,
)
# pyrefly: ignore [missing-import]
from evaluation.config import _CLASSIFICATION_SYSTEM_PROMPT, _CLASSIFICATION_PROMPT_VERSION
from app.observability import traced_span, record_llm_call, sanitize_tool_args, safe_set_attribute

load_dotenv()


def classify_ticket(message: str) -> dict:
    """Classifies a customer support ticket into category, priority, and sentiment using Groq.

    Args:
        message (str): The customer support ticket message text.

    Returns:
        dict: Dictionary with keys 'category', 'priority', and 'sentiment'.

    Raises:
        ValueError: If GROQ_API_KEY is missing, LLM returns empty response,
            invalid JSON, or missing required fields.
        RuntimeError: If the Groq API call fails.
    """
    with traced_span("llm.groq.classify") as span:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY environment variable is not set")

        client = Groq(api_key=api_key)

        system_prompt = _CLASSIFICATION_SYSTEM_PROMPT

        start = time.time()
        try:
            response = client.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": message},
                ],
                response_format={"type": "json_object"},
                temperature=0.0,
            )
        except Exception as err:
            raise RuntimeError(f"Groq API request failed: {err}") from err

        latency_ms = (time.time() - start) * 1000

        if span:
            record_llm_call(
                span,
                model="openai/gpt-oss-120b",
                prompt_version=_CLASSIFICATION_PROMPT_VERSION,
                latency_ms=latency_ms,
            )

        content = response.choices[0].message.content
        if not content:
            raise ValueError("Groq API returned an empty response content")

        try:
            data = json.loads(content)
        except json.JSONDecodeError as err:
            raise ValueError(f"Failed to parse invalid JSON from Groq LLM: {err}. Raw content: {content!r}") from err

        required_fields = {"category", "priority", "sentiment"}
        missing_fields = required_fields - set(data.keys())
        if missing_fields:
            raise ValueError(f"LLM JSON response is missing required keys: {missing_fields}. Data: {data}")

        if span:
            safe_set_attribute(span, "classification.category", data.get("category", ""))
            safe_set_attribute(span, "classification.priority", data.get("priority", ""))
            safe_set_attribute(span, "classification.sentiment", data.get("sentiment", ""))

        return data


# ── Tool schemas exposed to Groq ─────────────────────────────────────

ORDER_STATUS_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_order_status",
        "description": "Retrieves the current status of an order given its integer order ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "integer",
                    "description": "The unique integer ID of the order to query."
                }
            },
            "required": ["order_id"]
        }
    }
}

CREATE_REPLACEMENT_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_replacement_request",
        "description": (
            "Creates a replacement request for a damaged or defective order. "
            "Requires the order_id. The backend will verify ownership and eligibility."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "integer",
                    "description": "The unique integer ID of the order to request a replacement for."
                }
            },
            "required": ["order_id"]
        }
    }
}

ISSUE_REFUND_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "issue_refund",
        "description": (
            "Issues a refund for an order. Requires order_id and monetary amount in USD. "
            "The backend will validate order ownership and enforce risk approval rules."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "integer",
                    "description": "The unique integer ID of the order to refund."
                },
                "amount": {
                    "type": "number",
                    "description": "The refund amount in USD (must be greater than 0)."
                }
            },
            "required": ["order_id", "amount"]
        }
    }
}

AVAILABLE_TOOLS = [
    ORDER_STATUS_TOOL_SCHEMA,
    CREATE_REPLACEMENT_TOOL_SCHEMA,
    ISSUE_REFUND_TOOL_SCHEMA,
]

MAX_TOOL_ITERATIONS = 5


# ── Per-tool dispatch: validate → authorize → execute ────────────────

def _execute_tool_call(
    tool_name: str,
    tool_args: dict,
    authenticated_customer_id: int
) -> dict:
    """Dispatches a single tool call through validation, authorization, and execution.

    Args:
        tool_name (str): Name of the tool requested by the LLM.
        tool_args (dict): Raw arguments from the LLM.
        authenticated_customer_id (int): Trusted customer identity from the backend.

    Returns:
        dict with keys: validation_passed, authorization_passed, backend_executed, tool_result.
    """
    with traced_span(
        f"tool.{tool_name}",
        attributes={
            "tool_name": tool_name,
            "tool_args_hash": sanitize_tool_args(tool_args or {}),
            "customer_id": str(authenticated_customer_id),
        }
    ) as span:
        if tool_name == "get_order_status":
            # Step 1: Validate
            is_valid, validated, val_err = validate_get_order_status_args(tool_args or {})
            if not is_valid:
                result = {
                    "validation_passed": False,
                    "authorization_passed": None,
                    "backend_executed": False,
                    "tool_result": {"error": f"Backend Validation Error: {val_err}"},
                }
                if span:
                    safe_set_attribute(span, "validation_passed", False)
                return result

            # Step 2: Authorize + Execute
            is_auth, auth_err, order_data = authorize_and_get_order_status(
                authenticated_customer_id=authenticated_customer_id,
                order_id=validated.order_id,
            )
            if is_auth:
                result = {
                    "validation_passed": True,
                    "authorization_passed": True,
                    "backend_executed": True,
                    "tool_result": order_data,
                }
                if span:
                    safe_set_attribute(span, "validation_passed", True)
                    safe_set_attribute(span, "authorization_passed", True)
                    safe_set_attribute(span, "backend_executed", True)
                return result
            else:
                result = {
                    "validation_passed": True,
                    "authorization_passed": False,
                    "backend_executed": False,
                    "tool_result": {"error": f"Backend Authorization Error: {auth_err}"},
                }
                if span:
                    safe_set_attribute(span, "validation_passed", True)
                    safe_set_attribute(span, "authorization_passed", False)
                    safe_set_attribute(span, "backend_executed", False)
                return result

        elif tool_name == "create_replacement_request":
            # Step 1: Validate
            is_valid, validated, val_err = validate_create_replacement_request_args(tool_args or {})
            if not is_valid:
                result = {
                    "validation_passed": False,
                    "authorization_passed": None,
                    "backend_executed": False,
                    "tool_result": {"error": f"Backend Validation Error: {val_err}"},
                }
                if span:
                    safe_set_attribute(span, "validation_passed", False)
                return result

            # Step 2: Execute (auth + eligibility + duplicate check are internal)
            result_data = create_replacement_request(
                order_id=validated.order_id,
                authenticated_customer_id=authenticated_customer_id,
            )

            # Determine outcome from result
            if "error" in result_data:
                if "Unauthorized" in result_data["error"]:
                    result = {
                        "validation_passed": True,
                        "authorization_passed": False,
                        "backend_executed": False,
                        "tool_result": result_data,
                    }
                    if span:
                        safe_set_attribute(span, "validation_passed", True)
                        safe_set_attribute(span, "authorization_passed", False)
                        safe_set_attribute(span, "backend_executed", False)
                    return result
                # Not-found, not-eligible, or duplicate — auth was not the blocker
                result = {
                    "validation_passed": True,
                    "authorization_passed": True,
                    "backend_executed": False,
                    "tool_result": result_data,
                }
                if span:
                    safe_set_attribute(span, "validation_passed", True)
                    safe_set_attribute(span, "authorization_passed", True)
                    safe_set_attribute(span, "backend_executed", False)
                return result
            # Success
            result = {
                "validation_passed": True,
                "authorization_passed": True,
                "backend_executed": True,
                "tool_result": result_data,
            }
            if span:
                safe_set_attribute(span, "validation_passed", True)
                safe_set_attribute(span, "authorization_passed", True)
                safe_set_attribute(span, "backend_executed", True)
            return result

        elif tool_name == "issue_refund":
            # Step 1: Validate
            is_valid, validated, val_err = validate_issue_refund_args(tool_args or {})
            if not is_valid:
                result = {
                    "validation_passed": False,
                    "authorization_passed": None,
                    "backend_executed": False,
                    "tool_result": {"error": f"Backend Validation Error: {val_err}"},
                }
                if span:
                    safe_set_attribute(span, "validation_passed", False)
                return result

            # Step 2: Execute (auth + duplicate + risk rules handled internally)
            result_data = issue_refund(
                order_id=validated.order_id,
                amount=validated.amount,
                authenticated_customer_id=authenticated_customer_id,
            )

            if "error" in result_data:
                if "Unauthorized" in result_data["error"]:
                    result = {
                        "validation_passed": True,
                        "authorization_passed": False,
                        "backend_executed": False,
                        "tool_result": result_data,
                    }
                    if span:
                        safe_set_attribute(span, "validation_passed", True)
                        safe_set_attribute(span, "authorization_passed", False)
                        safe_set_attribute(span, "backend_executed", False)
                    return result
                result = {
                    "validation_passed": True,
                    "authorization_passed": True,
                    "backend_executed": False,
                    "tool_result": result_data,
                }
                if span:
                    safe_set_attribute(span, "validation_passed", True)
                    safe_set_attribute(span, "authorization_passed", True)
                    safe_set_attribute(span, "backend_executed", False)
                return result

            executed = (result_data.get("status") == "COMPLETED")
            approval_required = (result_data.get("status") == "PENDING_APPROVAL")

            result = {
                "validation_passed": True,
                "authorization_passed": True,
                "backend_executed": executed,
                "tool_result": result_data,
            }
            if span:
                safe_set_attribute(span, "validation_passed", True)
                safe_set_attribute(span, "authorization_passed", True)
                safe_set_attribute(span, "backend_executed", executed)
                if approval_required:
                    safe_set_attribute(span, "approval_required", True)
                    if "approval_id" in result_data:
                        safe_set_attribute(span, "approval_id", result_data["approval_id"])
            return result

        else:
            result = {
                "validation_passed": False,
                "authorization_passed": False,
                "backend_executed": False,
                "tool_result": {"error": f"Unknown tool '{tool_name}'"},
            }
            if span:
                safe_set_attribute(span, "validation_passed", False)
            return result
        return result


# ── Multi-step tool-calling loop ─────────────────────────────────────

def process_customer_message_with_tools(
    message: str,
    authenticated_customer_id: int = 1,
    max_tool_iterations: int = MAX_TOOL_ITERATIONS,
) -> dict:
    """Processes a customer message using multi-step Groq tool calling.

    The LLM may request zero, one, or multiple tool calls in sequence.
    After each tool execution the result is returned to the LLM so it
    can decide the next step.  The loop exits when the LLM produces a
    final assistant response or the iteration cap is reached.

    Args:
        message (str): Customer inquiry message.
        authenticated_customer_id (int): Trusted customer identity.
        max_tool_iterations (int): Hard cap on tool-calling rounds.

    Returns:
        dict: Detailed log with original_message, authenticated_customer_id,
              tool_calls (list), total_iterations, and final_response.
    """
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY environment variable is not set")

    client = Groq(api_key=api_key)

    system_prompt = (
        "You are a helpful customer support AI assistant for SupportFlow. "
        "Answer customer questions politely and concisely. "
        "If the customer asks about an order status and provides an order ID, "
        "use the 'get_order_status' tool to look it up. "
        "If the customer wants a replacement for a damaged or defective order, "
        "first check the order status with 'get_order_status', then ALWAYS use "
        "'create_replacement_request' to attempt the replacement. "
        "If the customer requests a refund for an order, use the 'issue_refund' tool "
        "with the order_id and requested amount. "
        "If the 'issue_refund' tool returns status 'PENDING_APPROVAL', inform the "
        "customer clearly that their refund request has been submitted for human "
        "approval and has NOT been issued yet. Never claim a refund was completed "
        "if approval is pending. "
        "You must NOT decide whether an order is eligible or whether approval is required — "
        "the backend will make that determination. Always call the tool and "
        "relay the backend result to the customer. "
        "Do not invent or guess order details. "
        "Do not fabricate tool results."
    )


    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": message},
    ]

    tool_call_log = []
    iteration = 0
    final_response = ""

    while iteration < max_tool_iterations:
        try:
            response = client.chat.completions.create(
                model="openai/gpt-oss-120b",
                messages=messages,
                tools=AVAILABLE_TOOLS,
                tool_choice="auto",
                temperature=0.0,
            )
        except Exception as err:
            raise RuntimeError(
                f"Groq API request failed at iteration {iteration}: {err}"
            ) from err

        response_message = response.choices[0].message

        # ── No tool calls → final assistant response ─────────────
        if not response_message.tool_calls:
            final_response = response_message.content or ""
            break

        # ── Process each tool call in this round ─────────────────
        messages.append(response_message)

        for tool_call in response_message.tool_calls:
            tool_name = tool_call.function.name
            try:
                tool_args = json.loads(tool_call.function.arguments)
            except Exception:
                tool_args = {"raw": tool_call.function.arguments}

            # Dispatch through backend validation + auth + execution
            execution = _execute_tool_call(
                tool_name, tool_args, authenticated_customer_id
            )

            # Record for debugging / reporting
            tool_call_log.append({
                "iteration": iteration + 1,
                "tool_name": tool_name,
                "tool_args": tool_args,
                "validation_passed": execution["validation_passed"],
                "authorization_passed": execution["authorization_passed"],
                "backend_executed": execution["backend_executed"],
                "tool_result": execution["tool_result"],
            })

            # Send result back to the LLM
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(execution["tool_result"]),
            })

        iteration += 1
    else:
        # Loop exhausted without a final text response
        final_response = (
            "[ERROR] Maximum tool-call iterations reached without a final response."
        )

    return {
        "original_message": message,
        "authenticated_customer_id": authenticated_customer_id,
        "tool_calls": tool_call_log,
        "total_iterations": iteration,
        "final_response": final_response,
    }
