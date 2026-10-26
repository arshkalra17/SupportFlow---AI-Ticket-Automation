"""LangGraph orchestration layer for SupportFlow.

Constructs a stateful workflow graph that orchestrates the existing
SupportFlow capabilities:  classification, RAG retrieval, tool execution,
approval checking, and response generation.

LangGraph is the ORCHESTRATOR — all business logic, validation,
authorization, and database operations remain in the existing service
modules (tools.py, approval.py, kb.py, rag.py, llm.py).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from typing import TypedDict

# pyrefly: ignore [missing-import]
from dotenv import load_dotenv
# pyrefly: ignore [missing-import]
from app.llm_client import chat_completion
# pyrefly: ignore [missing-import]
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command, interrupt

# Reuse existing SupportFlow capabilities — NO duplication
from app.llm import classify_ticket, _execute_tool_call, AVAILABLE_TOOLS, AGENT_TOOLS, POLICY_NOT_FOUND_MESSAGE
from app.action_guard import check_action
from app.kb import search_knowledge_base
from app.rag import answer_with_knowledge_base, RAG_SIMILARITY_THRESHOLD
from app.observability import traced_span, safe_set_attribute
from app.checkpoint import get_checkpointer
from app.database import SessionLocal
from app.models import Approval

load_dotenv()

logger = logging.getLogger("supportflow.graph")

# ── Configuration ─────────────────────────────────────────────────────
MAX_TOOL_ITERATIONS = 5

# router (default): classify_node labels the message and route_after_classify
#   sends it to either RAG or the tool loop.
# agent: no classification on the request path; the tool loop gets a
#   search_policy tool and decides for itself whether to read policy, call
#   order tools, or both. evaluation/golden_eval.py --routing-mode compares them.
ROUTING_MODES = ("router", "agent")


def routing_mode() -> str:
    mode = os.getenv("ROUTING_MODE", "router").strip().lower()
    if mode not in ROUTING_MODES:
        raise ValueError(f"ROUTING_MODE must be one of {ROUTING_MODES}, got {mode!r}")
    return mode

# Categories that indicate the customer is asking a knowledge/policy
# question rather than requesting a specific action on an order.
KNOWLEDGE_CATEGORIES = {
    "General Inquiry",
    "Shipping Inquiry",
    "Policy Question",
    "general inquiry",
    "shipping inquiry",
    "policy question",
}

# "order 7003", "order #7003", "#7003" — order ids are 3+ digits.
_ORDER_NUMBER_PATTERN = re.compile(r"(?:\border\s*#?\s*|#)\d{3,}\b")

# "my orders", "my delivered orders", "my open support tickets",
# "do I have any open tickets" — plural, so it doesn't catch hypotheticals
# like "if my order already shipped".
_MY_ORDERS_PATTERN = re.compile(
    r"\b(?:my|do\s+i\s+have\s+any)\s+(?:\w+\s+){0,2}(?:orders|tickets)\b"
)

# "If my order already shipped, ...", "What if a parcel ...", "Suppose I ..."
# — a hypothetical asks what the rules are, not for action on an order.
_HYPOTHETICAL_PATTERN = re.compile(r"^\s*(?:if|what\s+if|suppose|supposing|say|in\s+case)\b")

# Categories that typically require tool execution
ACTION_CATEGORIES = {
    "Order Issue",
    "Refund Request",
    "Billing",
    "Technical Support",
    "order issue",
    "refund request",
    "billing",
    "technical support",
}


# ═══════════════════════════════════════════════════════════════════════
# GRAPH STATE
# ═══════════════════════════════════════════════════════════════════════

class SupportFlowState(TypedDict):
    """Typed state flowing through the LangGraph workflow.

    Contains only serializable values needed for orchestration.
    Durable state remains in PostgreSQL.
    """
    customer_message: str
    authenticated_customer_id: int
    ticket_id: int | None
    
    # Conversation context (Stage 12)
    conversation_id: int
    conversation_history: list[dict]  # Recent messages: [{role, content, created_at}, ...]
    active_order_id: int | None
    active_ticket_id: int | None
    last_action: str | None
    
    # Workflow state
    classification: dict | None
    retrieved_documents: list[dict] | None
    rag_answer: str | None
    tool_calls: list[dict]
    tool_iterations: int
    approval_status: str | None
    final_response: str
    error: str | None
    execution_log: list[str]


# ═══════════════════════════════════════════════════════════════════════
# NODE FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════

def classify_node(state: SupportFlowState) -> dict:
    """Classifies the customer message into category/priority/sentiment.

    Passes conversation context to classification for context-aware routing.
    
    Reuses: classify_ticket() from app.llm
    """
    with traced_span("supportflow.graph.classify") as span:
        log = list(state.get("execution_log", []))
        log.append("→ ENTERED: classify_node")

        try:
            start = time.time()
            
            # Pass conversation context to classifier
            classification = classify_ticket(
                message=state["customer_message"],
                active_order_id=state.get("active_order_id"),
                active_ticket_id=state.get("active_ticket_id"),
                last_action=state.get("last_action"),
                recent_messages=state.get("conversation_history"),
            )
            
            latency_ms = (time.time() - start) * 1000

            if span:
                safe_set_attribute(span, "classification.category", classification.get("category", ""))
                safe_set_attribute(span, "classification.priority", classification.get("priority", ""))
                safe_set_attribute(span, "classification.sentiment", classification.get("sentiment", ""))
                safe_set_attribute(span, "latency_ms", latency_ms)

            log.append(f"  classification: {classification}")
            return {
                "classification": classification,
                "execution_log": log,
            }
        except Exception as err:
            log.append(f"  ERROR in classify_node: {err}")
            return {
                "classification": None,
                "error": f"Classification failed: {err}",
                "execution_log": log,
            }


def retrieve_knowledge_node(state: SupportFlowState) -> dict:
    """Retrieves relevant policy documents and generates a grounded answer.

    Reuses: answer_with_knowledge_base() from app.rag
    (which internally calls search_knowledge_base() from app.kb)
    """
    with traced_span("supportflow.graph.retrieve_knowledge") as span:
        log = list(state.get("execution_log", []))
        log.append("→ ENTERED: retrieve_knowledge_node")

        try:
            start = time.time()
            rag_result = answer_with_knowledge_base(state["customer_message"])
            latency_ms = (time.time() - start) * 1000

            retrieved_docs = rag_result.get("retrieved_documents", [])
            relevant = rag_result.get("relevant_context", False)

            log.append(f"  relevant_context: {relevant}")
            log.append(f"  retrieved_documents: {len(retrieved_docs)}")
            for doc in retrieved_docs:
                log.append(f"    - \"{doc['title']}\" (sim={doc['cosine_similarity']:.4f})")

            if span:
                safe_set_attribute(span, "relevant_context", relevant)
                safe_set_attribute(span, "documents_retrieved", len(retrieved_docs))
                safe_set_attribute(span, "latency_ms", latency_ms)

            if relevant:
                log.append(f"  rag_answer generated by LLM")
                return {
                    "retrieved_documents": retrieved_docs,
                    "rag_answer": rag_result["answer"],
                    "final_response": rag_result["answer"],
                    "execution_log": log,
                }
            else:
                # No relevant documents found - use RAG's built-in refusal
                log.append("  no relevant policy found — using RAG refusal")
                return {
                    "retrieved_documents": retrieved_docs,
                    "rag_answer": rag_result["answer"],
                    "final_response": rag_result["answer"],
                    "execution_log": log,
                }

        except Exception as err:
            # RAG infrastructure failure - fail closed with deterministic refusal
            log.append(f"  ERROR in retrieve_knowledge_node: {err}")
            log.append("  RAG infrastructure failed — returning refusal (fail closed)")
            return {
                "retrieved_documents": [],
                "rag_answer": None,
                "final_response": (
                    "I apologize, but I'm unable to access our knowledge base at this moment. "
                    "Please contact a human support agent who can help you with your question."
                ),
                "error": f"RAG retrieval failed: {err}",
                "execution_log": log,
            }


def tool_execution_node(state: SupportFlowState) -> dict:
    """Sends the message to Groq with tool schemas and executes requested tools.

    Reuses:
    - Groq tool-calling API (same model + tool schemas from app.llm)
    - _execute_tool_call() from app.llm for validation/authorization/execution
    """
    with traced_span("supportflow.graph.tool_execution") as span:
        log = list(state.get("execution_log", []))
        tool_calls = list(state.get("tool_calls", []))
        iterations = state.get("tool_iterations", 0)

        log.append(f"→ ENTERED: tool_execution_node (iteration {iterations + 1})")

        if span:
            safe_set_attribute(span, "tool_iteration", iterations + 1)

        # Guard: iteration limit
        if iterations >= MAX_TOOL_ITERATIONS:
            log.append("  ERROR: max tool iterations reached")
            return {
                "error": "Maximum tool-call iterations reached",
                "tool_iterations": iterations,
                "execution_log": log,
            }

        # Build messages from accumulated context
        # Kept short on purpose: this prompt and the tool schemas are resent
        # on every tool-loop call and dominate the token budget.
        system_prompt = (
            "You are SupportFlow's customer support assistant. Be polite and concise. "
            "Use the right tool before answering, and report tool results exactly.\n\n"
            "Rules:\n"
            "- Never invent order, ticket, status, date or item details. If a field is null "
            "or a delivery estimate is unavailable, say that information is not available.\n"
            "- Read-only tools never change anything. check_cancellation_eligibility only "
            "checks: if eligible, say the order CAN be cancelled but has NOT been cancelled.\n"
            "- Claim a change only when the tool result confirms it: issue_refund status "
            "COMPLETED = refund issued; PENDING_APPROVAL = submitted for human approval and "
            "NOT issued yet; cancel_order COMPLETED = cancelled, ALREADY_CANCELLED = was "
            "already cancelled; create_replacement_request PENDING = request created. On an "
            "error, report it and do not claim the action happened.\n"
            "- Only refund, cancel or replace when the customer asks you to. A question about "
            "eligibility, cost or what would happen gets read-only tools and an offer to act.\n"
            "- If authorization fails, say the resource could not be accessed. Identity is "
            "handled by the backend: never ask for or accept a customer ID.\n"
        )
        agent = routing_mode() == "agent"
        if agent:
            system_prompt += (
                "- For any question about company rules (time windows, fees, eligibility, how "
                "something works) call search_policy and answer only from the sections it "
                "returns; never state policy from memory. If it finds nothing, reply: "
                f"\"{POLICY_NOT_FOUND_MESSAGE}\" and offer a human agent. A question about one "
                "order can need both: order tools for its data, search_policy for the rule.\n"
                "- Politely decline requests unrelated to shopping with SupportFlow.\n"
            )
        else:
            system_prompt += (
                "- No tool returns company policy. Never state policy terms (time windows, fees, "
                "return labels, eligibility rules) from memory; offer to check a specific order "
                "instead, or say the policy team can confirm the details.\n"
            )

        # Add conversation history context if available
        conversation_history = state.get("conversation_history", [])
        if conversation_history:
            # Include recent history (most recent first, reverse for chronological)
            history_lines = []
            for msg in reversed(conversation_history[:10]):  # 10 most recent (list is newest-first), chronological order
                history_lines.append(f"{msg['role'].capitalize()}: {msg['content']}")
            history_section = "\n\nRecent conversation:\n" + "\n".join(history_lines)
            system_prompt += history_section

        # Add active order context hint if available
        active_order_id = state.get("active_order_id")
        if active_order_id:
            system_prompt += f"\n\nContext: The customer is currently discussing Order #{active_order_id}. If they refer to 'it', 'that order', or 'this order', they likely mean Order #{active_order_id}."

        active_ticket_id = state.get("active_ticket_id")
        if active_ticket_id:
            system_prompt += f"\n\nContext: The customer is currently discussing Ticket #{active_ticket_id}. If they refer to 'my ticket', 'it', or 'that ticket', they likely mean Ticket #{active_ticket_id}."

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": state["customer_message"]},
        ]

        # Replay previous tool interactions so the LLM has full context
        for tc in tool_calls:
            # Simulate the assistant's tool_call message
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": tc.get("tool_call_id", f"call_{tc['iteration']}"),
                    "type": "function",
                    "function": {
                        "name": tc["tool_name"],
                        "arguments": json.dumps(tc["tool_args"]),
                    },
                }],
            })
            # Simulate the tool response
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("tool_call_id", f"call_{tc['iteration']}"),
                "content": json.dumps(tc["tool_result"]),
            })

        # Call the LLM with tools (Groq, or the fallback provider)
        try:
            result = chat_completion(
                messages=messages,
                tools=AGENT_TOOLS if agent else AVAILABLE_TOOLS,
                tool_choice="auto",
                temperature=0.0,
            )
        except Exception as err:
            log.append(f"  ERROR: LLM API failed: {err}")
            return {"error": f"LLM API failed: {err}", "execution_log": log}
        response = result.response
        log.append(f"  LLM provider: {result.provider} ({result.model})")
        if span:
            safe_set_attribute(span, "llm.provider", result.provider)

        response_message = response.choices[0].message

        # ── No tool calls → LLM produced final response ──────────────
        if not response_message.tool_calls:
            final = response_message.content or ""
            log.append(f"  LLM produced final response (no more tools)")
            return {
                "final_response": final,
                "tool_iterations": iterations + 1,
                "execution_log": log,
            }

        # ── Process tool calls ────────────────────────────────────────
        approval_status = state.get("approval_status")
        final_response = None
        retrieved_documents = state.get("retrieved_documents")
        updated_active_order_id = state.get("active_order_id")
        updated_last_action = state.get("last_action")

        for tc in response_message.tool_calls:
            tool_name = tc.function.name
            try:
                tool_args = json.loads(tc.function.arguments)
            except Exception:
                tool_args = {"raw": tc.function.arguments}

            log.append(f"  tool requested: {tool_name}({tool_args})")

            # A refund, cancellation or replacement runs only when the customer asked for it
            guarded = check_action(tool_name, state["customer_message"])
            if guarded is not None:
                log.append(f"    action guard: not executed ({guarded.get('error') or guarded['status']})")
                execution = {
                    "validation_passed": False,
                    "authorization_passed": None,
                    "backend_executed": False,
                    "tool_result": guarded,
                }
            else:
                # Dispatch through existing backend validation + auth + execution
                execution = _execute_tool_call(
                    tool_name, tool_args, state["authenticated_customer_id"]
                )

            log.append(f"    validation: {'✅' if execution['validation_passed'] else '❌'}")
            if execution["authorization_passed"] is not None:
                log.append(f"    authorization: {'✅' if execution['authorization_passed'] else '❌'}")
            log.append(f"    executed: {'✅' if execution['backend_executed'] else '❌'}")

            tool_result = execution["tool_result"]

            # Policy the agent read: kept for the reply's audit trail and the eval judge
            if tool_name == "search_policy" and execution["backend_executed"]:
                retrieved_documents = list(retrieved_documents or []) + execution.get("documents", [])

            # Check for approval-required status
            if isinstance(tool_result, dict) and tool_result.get("status") == "PENDING_APPROVAL":
                approval_status = "PENDING_APPROVAL"
                log.append(f"    → APPROVAL REQUIRED (approval_id={tool_result.get('approval_id')})")

                # Generate deterministic response for PENDING_APPROVAL
                # Extract order_id and amount from tool_args
                order_id = tool_args.get("order_id", "unknown")
                amount = tool_args.get("amount")

                response_parts = [
                    f"Your refund request for order #{order_id}"
                ]
                if amount is not None:
                    response_parts[0] = f"Your ${amount:,.2f} refund request for order #{order_id}"

                response_parts.append(
                    "has been submitted for human approval. "
                    "No refund has been executed yet."
                )

                approval_id = tool_result.get("approval_id")
                if approval_id:
                    response_parts.append(f" Your approval ID is {approval_id}.")

                final_response = " ".join(response_parts)
                log.append(f"  → Generated deterministic PENDING_APPROVAL response")

            # Update active_order_id if order-related tool succeeded
            if execution["backend_executed"] and tool_name in (
                "get_order_status", "issue_refund", "create_replacement_request",
                "get_order_details", "check_cancellation_eligibility",
                "get_delivery_estimate", "cancel_order",
            ):
                if "order_id" in tool_args:
                    updated_active_order_id = tool_args["order_id"]
                    log.append(f"    → Updated active_order_id to {updated_active_order_id}")
                updated_last_action = tool_name
                log.append(f"    → Updated last_action to {updated_last_action}")

            tool_calls.append({
                "iteration": iterations + 1,
                "tool_name": tool_name,
                "tool_args": tool_args,
                "tool_call_id": tc.id,
                "validation_passed": execution["validation_passed"],
                "authorization_passed": execution["authorization_passed"],
                "backend_executed": execution["backend_executed"],
                "tool_result": tool_result,
            })

        result = {
            "tool_calls": tool_calls,
            "tool_iterations": iterations + 1,
            "approval_status": approval_status,
            "execution_log": log,
        }
        if retrieved_documents is not state.get("retrieved_documents"):
            result["retrieved_documents"] = retrieved_documents

        # Update active order and last action in state
        if updated_active_order_id != state.get("active_order_id"):
            result["active_order_id"] = updated_active_order_id
        if updated_last_action != state.get("last_action"):
            result["last_action"] = updated_last_action

        # If we generated a final response for PENDING_APPROVAL, include it
        if final_response:
            result["final_response"] = final_response

        return result


def _pending_approvals(state: SupportFlowState) -> list[dict]:
    """Approval requests created by tool calls in this run."""
    pending = []
    for tc in state.get("tool_calls") or []:
        result = tc.get("tool_result")
        if isinstance(result, dict) and result.get("status") == "PENDING_APPROVAL" and result.get("approval_id"):
            pending.append({
                "approval_id": result["approval_id"],
                "action_type": "REFUND" if tc.get("tool_name") == "issue_refund" else tc.get("tool_name"),
                "order_id": (tc.get("tool_args") or {}).get("order_id"),
                "amount": (tc.get("tool_args") or {}).get("amount"),
            })
    return pending


def _decision_message(request: dict, decision: dict) -> str:
    """Customer-facing outcome, built from the backend's result rather than
    generated, so it can't claim a refund that didn't happen."""
    what = f"your refund request for order #{request['order_id']}"
    amount = request.get("amount")
    money = f"${amount:,.2f} " if isinstance(amount, (int, float)) else ""
    if not decision.get("approved"):
        return f"Update on {what}: after review it was not approved, so no refund has been issued."
    if decision.get("executed"):
        return f"Update on {what}: it was approved and your {money}refund has been issued."
    return (
        f"Update on {what}: it was approved, but the refund could not be completed "
        f"({decision.get('execution_error') or 'unknown error'}). A support agent will follow up."
    )


def approval_check_node(state: SupportFlowState) -> dict:
    """Pauses the run until an admin approves or rejects each pending request.

    interrupt() saves the run in the checkpointer and stops it. The admin
    endpoint executes (or rejects) the action through app.approval, under a
    row lock, then resumes this run with the outcome via
    resume_after_approval(); this node only turns that outcome into the
    customer reply. Execution stays outside the graph on purpose: a node
    re-runs from the top on resume, so anything with side effects before
    an interrupt would run twice.
    """
    with traced_span("supportflow.graph.approval_check") as span:
        log = list(state.get("execution_log", []))
        log.append("→ ENTERED: approval_check_node")
        pending = _pending_approvals(state)
        if span:
            safe_set_attribute(span, "approval.pending_count", len(pending))
        if not pending:
            return {"execution_log": log}

        messages, outcomes = [], []
        for request in pending:
            decision = interrupt(request)
            log.append(f"  resumed: approval #{request['approval_id']} -> {decision}")
            messages.append(_decision_message(request, decision))
            if not decision.get("approved"):
                outcomes.append("REJECTED")
            elif decision.get("executed"):
                outcomes.append("APPROVED")
            else:
                outcomes.append("FAILED")

        status = next((s for s in ("REJECTED", "FAILED") if s in outcomes), "APPROVED")
        return {"execution_log": log, "approval_status": status, "final_response": " ".join(messages)}


def generate_response_node(state: SupportFlowState) -> dict:
    """Generates the final customer-facing response.

    If a response was already produced (by RAG or tool execution),
    it is used directly. Otherwise, calls Groq for a final summary.

    Reuses: Groq LLM for response generation.
    """
    with traced_span("supportflow.graph.generate_response") as span:
        log = list(state.get("execution_log", []))
        log.append("→ ENTERED: generate_response_node")

        # If we already have a final response (from RAG or tool loop), use it
        existing = state.get("final_response", "")
        if existing:
            log.append("  using existing final_response")
            if span:
                safe_set_attribute(span, "response_source", "existing")
            return {"execution_log": log}

        # Generate from accumulated context
        context_parts = [f"Customer message: {state['customer_message']}"]
        if state.get("classification"):
            context_parts.append(f"Classification: {state['classification']}")
        if state.get("error"):
            context_parts.append(f"Error: {state['error']}")

        try:
            response = chat_completion(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a helpful customer support assistant. "
                            "Generate a helpful, professional response to the customer. "
                            "If there was an error, explain it politely."
                        ),
                    },
                    {"role": "user", "content": "\n".join(context_parts)},
                ],
                temperature=0.0,
            ).response
            final = response.choices[0].message.content or ""
            log.append("  generated final response via LLM")
            if span:
                safe_set_attribute(span, "response_source", "generated")
            return {"final_response": final.strip(), "execution_log": log}

        except Exception as err:
            log.append(f"  ERROR generating response: {err}")
            return {
                "final_response": "I apologize, but I encountered an issue processing your request.",
                "execution_log": log,
            }


def error_node(state: SupportFlowState) -> dict:
    """Captures error state and produces a safe customer-facing response."""
    log = list(state.get("execution_log", []))
    log.append(f"→ ENTERED: error_node (error: {state.get('error')})")

    return {
        "final_response": (
            "I apologize, but I encountered an issue processing your request. "
            "Please try again or contact a human support agent."
        ),
        "execution_log": log,
    }


# ═══════════════════════════════════════════════════════════════════════
# CONDITIONAL EDGES (routing functions)
# ═══════════════════════════════════════════════════════════════════════

def route_after_knowledge(state: SupportFlowState) -> str:
    """Routes after RAG retrieval.
    
    - If final_response is set (RAG succeeded or returned refusal) → END
    - This prevents RAG failures from falling through to generic LLM
    """
    # RAG node always sets final_response (either grounded answer, refusal, or error message)
    # No need to call generate_response_node for knowledge queries
    return END


def route_after_classify(state: SupportFlowState) -> str:
    """Routes after classification based on the detected category.

    Knowledge/policy questions → RAG retrieval
    Action-oriented categories → tool execution
    Errors → error node

    STAGE 12 FIX: Routing safeguard for implicit order references when
    active_order_id is set and message contains order action keywords.

    TOOLS EXPANSION FIX: Routing safeguard for order-list requests
    (e.g., "Show me my orders") independent of active order context.
    """
    if state.get("error"):
        return "error_node"

    classification = state.get("classification", {})
    category = (classification.get("category", "") or "").strip()
    category_lower = category.lower()

    message_lower = state.get("customer_message", "").lower()
    active_order_id = state.get("active_order_id")

    logger.debug(
        "route_after_classify: active_order_id=%s category=%r message=%r",
        active_order_id, category, state.get("customer_message"),
    )

    # ROUTING SAFEGUARD 1: Customer resource-list requests (orders and tickets)
    # Catches "Show me my orders", "List my tickets", etc. when classified as
    # General Inquiry instead of an action category.
    if "general inquiry" in category_lower:
        CUSTOMER_LIST_PATTERNS = [
            # Order list patterns
            "show me my order",
            "show my order",
            "list my order",
            "see my order",
            "view my order",
            "what orders do i have",
            "what orders have i",
            "do i have any order",
            "can i see my order",
            "display my order",
            "get my order",
            "my recent order",
            "my past order",
            # Ticket list patterns
            "show me my ticket",
            "show my ticket",
            "list my ticket",
            "see my ticket",
            "view my ticket",
            "what tickets do i have",
            "do i have any ticket",
            "can i see my ticket",
            "my support ticket",
            "my open ticket",
            "my recent ticket",
        ]
        POLICY_KEYWORDS = ["policy", "policies", "rule", "rules", "guideline", "guidelines", "how long", "how does"]

        is_policy_question = any(keyword in message_lower for keyword in POLICY_KEYWORDS)

        if not is_policy_question:
            is_list_request = (
                any(pattern in message_lower for pattern in CUSTOMER_LIST_PATTERNS)
                or _MY_ORDERS_PATTERN.search(message_lower) is not None
            )
            logger.debug("route_after_classify: is_list_request=%s", is_list_request)
            if is_list_request:
                logger.debug("route_after_classify: CUSTOMER-LIST SAFEGUARD → tool_execution_node")
                return "tool_execution_node"

    # ROUTING SAFEGUARD 2: Implicit order-specific follow-ups (Stage 12)
    if active_order_id and "general inquiry" in category_lower:
        ORDER_ACTION_KEYWORDS = [
            "status", "cancel", "refund", "replace", "track", "ship",
            "deliver", "return", "exchange", "modify", "update", "change",
            "details", "items", "total", "arrive",
        ]
        POLICY_KEYWORDS = ["policy", "policies", "rule", "rules", "guideline", "guidelines"]

        is_policy_question = any(keyword in message_lower for keyword in POLICY_KEYWORDS)
        logger.debug("route_after_classify: Stage12 is_policy_question=%s", is_policy_question)

        if not is_policy_question:
            has_order_action = any(keyword in message_lower for keyword in ORDER_ACTION_KEYWORDS)
            is_short_contextual = len(message_lower.split()) <= 6 and has_order_action
            logger.debug(
                "route_after_classify: has_order_action=%s is_short_contextual=%s",
                has_order_action, is_short_contextual,
            )
            if has_order_action or is_short_contextual:
                logger.debug("route_after_classify: STAGE12 SAFEGUARD → tool_execution_node")
                return "tool_execution_node"

    # ROUTING SAFEGUARD 3: an explicit order number ("order 7003", "#7003")
    # means the answer depends on that order's data, which RAG cannot see.
    if "general inquiry" in category_lower and _ORDER_NUMBER_PATTERN.search(message_lower):
        POLICY_KEYWORDS = ["policy", "policies", "rule", "rules", "guideline", "guidelines"]
        if not any(keyword in message_lower for keyword in POLICY_KEYWORDS):
            logger.debug("route_after_classify: ORDER-NUMBER SAFEGUARD → tool_execution_node")
            return "tool_execution_node"

    # ROUTING SAFEGUARD 4: a hypothetical about no particular order is a policy
    # question, whatever the classifier said (the small classifier often
    # labels "If my order already shipped, ..." an Order Issue). An order
    # number or an order under discussion means it is about that order.
    if (
        "order issue" in category_lower
        and not active_order_id
        and _HYPOTHETICAL_PATTERN.search(message_lower)
        and not _ORDER_NUMBER_PATTERN.search(message_lower)
        and not _MY_ORDERS_PATTERN.search(message_lower)
    ):
        logger.debug("route_after_classify: HYPOTHETICAL SAFEGUARD → retrieve_knowledge_node")
        return "retrieve_knowledge_node"

    # Standard classification routing
    if any(kc.lower() in category_lower for kc in KNOWLEDGE_CATEGORIES):
        logger.debug("route_after_classify: → retrieve_knowledge_node")
        return "retrieve_knowledge_node"

    logger.debug("route_after_classify: → tool_execution_node (default)")
    return "tool_execution_node"


def route_after_tool(state: SupportFlowState) -> str:
    """Routes after tool execution.

    - If approval is required → approval_check_node
    - If LLM already produced a final response → generate_response_node
    - If more tool calls needed and under limit → tool_execution_node (loop)
    - Otherwise → generate_response_node
    """
    if state.get("error"):
        return "error_node"

    # If approval was triggered, route through approval check
    if state.get("approval_status") == "PENDING_APPROVAL":
        return "approval_check_node"

    # If we got a final response from the tool node, we're done
    if state.get("final_response"):
        return "generate_response_node"

    # If under iteration limit and no final response yet, loop back
    if state.get("tool_iterations", 0) < MAX_TOOL_ITERATIONS:
        return "tool_execution_node"

    # Safety: hit iteration limit
    return "generate_response_node"


def route_after_approval(state: SupportFlowState) -> str:
    """Routes after approval check → always to response generation."""
    return "generate_response_node"


# ═══════════════════════════════════════════════════════════════════════
# GRAPH CONSTRUCTION
# ═══════════════════════════════════════════════════════════════════════

def build_supportflow_graph(checkpointer=None, mode: str | None = None):
    """Constructs and compiles the SupportFlow LangGraph workflow.

    Graph structure (rendered from the code in docs/langgraph.md, via
    scripts/export_graph.py):
        START → classify_node
          → [route_after_classify]
             ├── retrieve_knowledge_node → END
             ├── tool_execution_node
             │     → [route_after_tool]
             │        ├── tool_execution_node (loop, max 5)
             │        ├── approval_check_node (pauses until an admin decides)
             │        │     → generate_response_node → END
             │        ├── error_node → END
             │        └── generate_response_node → END
             └── error_node → END

    checkpointer: required to run (approvals pause with interrupt());
    None is enough to draw the graph.
    mode: "router" (default, drawn above) or "agent", where START goes
    straight to tool_execution_node; None reads ROUTING_MODE.
    """
    mode = mode or routing_mode()
    graph = StateGraph(SupportFlowState)

    # ── Add nodes ─────────────────────────────────────────────────
    if mode == "router":
        graph.add_node("classify_node", classify_node)
        graph.add_node("retrieve_knowledge_node", retrieve_knowledge_node)
    graph.add_node("tool_execution_node", tool_execution_node)
    graph.add_node("approval_check_node", approval_check_node)
    graph.add_node("generate_response_node", generate_response_node)
    graph.add_node("error_node", error_node)

    # ── Add edges ─────────────────────────────────────────────────
    if mode == "router":
        # START → classify → RAG or tools
        graph.add_edge(START, "classify_node")
        graph.add_conditional_edges(
            "classify_node",
            route_after_classify,
            {
                "retrieve_knowledge_node": "retrieve_knowledge_node",
                "tool_execution_node": "tool_execution_node",
                "error_node": "error_node",
            },
        )
        # knowledge retrieval → END (no need for generate_response_node)
        graph.add_edge("retrieve_knowledge_node", END)
    else:
        # START → tool loop, which reads policy through search_policy when needed
        graph.add_edge(START, "tool_execution_node")

    # tool execution → conditional routing
    graph.add_conditional_edges(
        "tool_execution_node",
        route_after_tool,
        {
            "tool_execution_node": "tool_execution_node",
            "approval_check_node": "approval_check_node",
            "generate_response_node": "generate_response_node",
            "error_node": "error_node",
        },
    )

    # approval check → response
    graph.add_conditional_edges(
        "approval_check_node",
        route_after_approval,
        {
            "generate_response_node": "generate_response_node",
        },
    )

    # response → END
    graph.add_edge("generate_response_node", END)

    # error → END
    graph.add_edge("error_node", END)

    return graph.compile(checkpointer=checkpointer)


# Compiled graph singleton, rebuilt if the checkpointer or routing mode changed
_compiled_graph = None
_compiled_mode = None


def _get_graph():
    """Returns the compiled graph singleton."""
    global _compiled_graph, _compiled_mode
    mode = routing_mode()
    if (
        _compiled_graph is None
        or _compiled_graph.checkpointer is not get_checkpointer()
        or _compiled_mode != mode
    ):
        _compiled_graph = build_supportflow_graph(get_checkpointer(), mode)
        _compiled_mode = mode
    return _compiled_graph


def _thread_id(conversation_id: int | None) -> str:
    """One thread per conversation. Runs outside a conversation (evals,
    scripts) get a throwaway thread so their checkpoints never mix."""
    return f"conversation-{conversation_id}" if conversation_id else f"run-{uuid.uuid4().hex}"


def _record_pause(graph, thread_id: str) -> None:
    """Stores on each awaiting Approval where its run is paused, so the
    admin's decision resumes that exact run, even after a restart."""
    snapshot = graph.get_state({"configurable": {"thread_id": thread_id}})
    checkpoint_id = snapshot.config["configurable"]["checkpoint_id"]
    approval_ids = [i.value["approval_id"] for i in snapshot.interrupts if isinstance(i.value, dict)]
    if not approval_ids:
        return
    db = SessionLocal()
    try:
        for approval in db.query(Approval).filter(Approval.id.in_(approval_ids)):
            approval.graph_thread_id = thread_id
            approval.graph_checkpoint_id = checkpoint_id
        db.commit()
    finally:
        db.close()


def _finish(graph, result, thread_id: str) -> SupportFlowState:
    result = dict(result)
    if result.pop("__interrupt__", None):
        _record_pause(graph, thread_id)
    return result


# ═══════════════════════════════════════════════════════════════════════
# ENTRY FUNCTION
# ═══════════════════════════════════════════════════════════════════════

def run_supportflow(
    customer_message: str,
    authenticated_customer_id: int,
    ticket_id: int | None = None,
    conversation_id: int = 0,
    conversation_history: list[dict] | None = None,
    active_order_id: int | None = None,
    active_ticket_id: int | None = None,
    last_action: str | None = None,
) -> SupportFlowState:
    """Executes the SupportFlow LangGraph workflow.

    This is the main entry point for running a customer message through
    the full orchestration pipeline.  It is independent of FastAPI and
    can be tested standalone.

    Args:
        customer_message:         The customer's natural-language message.
        authenticated_customer_id: Trusted customer identity.
        ticket_id:                Optional ticket ID for context.
        conversation_id:          Conversation ID for context tracking.
        conversation_history:     Recent messages for multi-turn understanding.
        active_order_id:          Currently active order in conversation context.
        active_ticket_id:         Currently active ticket in conversation context.
        last_action:              Last action performed in conversation.

    Returns:
        SupportFlowState: Final workflow state with all accumulated context.
    """
    initial_state: SupportFlowState = {
        "customer_message": customer_message,
        "authenticated_customer_id": authenticated_customer_id,
        "ticket_id": ticket_id,
        "conversation_id": conversation_id,
        "conversation_history": conversation_history or [],
        "active_order_id": active_order_id,
        "active_ticket_id": active_ticket_id,
        "last_action": last_action,
        "classification": None,
        "retrieved_documents": None,
        "rag_answer": None,
        "tool_calls": [],
        "tool_iterations": 0,
        "approval_status": None,
        "final_response": "",
        "error": None,
        "execution_log": [],
    }

    graph = _get_graph()
    thread_id = _thread_id(conversation_id)
    result = graph.invoke(initial_state, {"configurable": {"thread_id": thread_id}})
    return _finish(graph, result, thread_id)


def resume_after_approval(approval_id: int, decision: dict) -> SupportFlowState | None:
    """Resumes the run paused on this approval with the admin's outcome.

    decision: {"approved": bool, "executed": bool, "execution_error": str | None},
    produced by app.approval after it has executed or rejected the action.
    Returns the run's final state, or None if no paused run is recorded
    for this approval (e.g. it was created outside the graph).

    The caller must only call this once per approval, after the approval's
    status changed under its row lock; that lock is what makes the
    decision, and so the resume, happen exactly once.
    """
    db = SessionLocal()
    try:
        approval = db.get(Approval, approval_id)
        if approval is None or not approval.graph_thread_id:
            return None
        thread_id, checkpoint_id = approval.graph_thread_id, approval.graph_checkpoint_id
    finally:
        db.close()

    graph = _get_graph()
    # Resume from the checkpoint where this approval paused, even if the
    # customer has sent more messages on the thread since.
    config = {"configurable": {"thread_id": thread_id, "checkpoint_id": checkpoint_id}}
    result = graph.invoke(Command(resume=decision), config)
    return _finish(graph, result, thread_id)
