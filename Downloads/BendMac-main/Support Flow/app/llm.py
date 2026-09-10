import json
import os
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
)

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
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY environment variable is not set")

    client = Groq(api_key=api_key)

    system_prompt = (
        "You are an AI customer support ticket classifier. "
        "Analyze the customer message and classify it into:\n"
        "- category (e.g., Technical Support, Billing, Order Issue, General Inquiry, Refund Request)\n"
        "- priority (e.g., LOW, MEDIUM, HIGH, URGENT)\n"
        "- sentiment (e.g., POSITIVE, NEUTRAL, NEGATIVE, FRUSTRATED)\n\n"
        "You MUST respond ONLY with a valid JSON object with no markdown formatting, preambles, or explanations. "
        "The JSON object MUST contain exactly these three keys: \"category\", \"priority\", and \"sentiment\"."
    )

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

AVAILABLE_TOOLS = [ORDER_STATUS_TOOL_SCHEMA, CREATE_REPLACEMENT_TOOL_SCHEMA]
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
    if tool_name == "get_order_status":
        # Step 1: Validate
        is_valid, validated, val_err = validate_get_order_status_args(tool_args or {})
        if not is_valid:
            return {
                "validation_passed": False,
                "authorization_passed": None,
                "backend_executed": False,
                "tool_result": {"error": f"Backend Validation Error: {val_err}"},
            }

        # Step 2: Authorize + Execute
        is_auth, auth_err, order_data = authorize_and_get_order_status(
            authenticated_customer_id=authenticated_customer_id,
            order_id=validated.order_id,
        )
        if is_auth:
            return {
                "validation_passed": True,
                "authorization_passed": True,
                "backend_executed": True,
                "tool_result": order_data,
            }
        else:
            return {
                "validation_passed": True,
                "authorization_passed": False,
                "backend_executed": False,
                "tool_result": {"error": f"Backend Authorization Error: {auth_err}"},
            }

    elif tool_name == "create_replacement_request":
        # Step 1: Validate
        is_valid, validated, val_err = validate_create_replacement_request_args(tool_args or {})
        if not is_valid:
            return {
                "validation_passed": False,
                "authorization_passed": None,
                "backend_executed": False,
                "tool_result": {"error": f"Backend Validation Error: {val_err}"},
            }

        # Step 2: Execute (auth + eligibility + duplicate check are internal)
        result = create_replacement_request(
            order_id=validated.order_id,
            authenticated_customer_id=authenticated_customer_id,
        )

        # Determine outcome from result
        if "error" in result:
            if "Unauthorized" in result["error"]:
                return {
                    "validation_passed": True,
                    "authorization_passed": False,
                    "backend_executed": False,
                    "tool_result": result,
                }
            # Not-found, not-eligible, or duplicate — auth was not the blocker
            return {
                "validation_passed": True,
                "authorization_passed": True,
                "backend_executed": False,
                "tool_result": result,
            }
        # Success
        return {
            "validation_passed": True,
            "authorization_passed": True,
            "backend_executed": True,
            "tool_result": result,
        }

    else:
        return {
            "validation_passed": False,
            "authorization_passed": False,
            "backend_executed": False,
            "tool_result": {"error": f"Unknown tool '{tool_name}'"},
        }


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
        "You must NOT decide whether an order is eligible for replacement — "
        "the backend will make that determination. Always call the tool and "
        "relay the result to the customer. "
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
