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
import os
import time
from typing import TypedDict

# pyrefly: ignore [missing-import]
from dotenv import load_dotenv
# pyrefly: ignore [missing-import]
from groq import Groq
# pyrefly: ignore [missing-import]
from langgraph.graph import StateGraph, START, END

# Reuse existing SupportFlow capabilities — NO duplication
from app.llm import classify_ticket, _execute_tool_call, AVAILABLE_TOOLS
from app.kb import search_knowledge_base
from app.rag import answer_with_knowledge_base, RAG_SIMILARITY_THRESHOLD
from app.observability import traced_span, safe_set_attribute

load_dotenv()

# ── Configuration ─────────────────────────────────────────────────────
MAX_TOOL_ITERATIONS = 5
GROQ_MODEL = "openai/gpt-oss-120b"

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

        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            log.append("  ERROR: GROQ_API_KEY not set")
            return {"error": "GROQ_API_KEY not set", "execution_log": log}

        client = Groq(api_key=api_key)

        # Build messages from accumulated context
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

        # Add conversation history context if available
        conversation_history = state.get("conversation_history", [])
        if conversation_history:
            # Include recent history (most recent first, reverse for chronological)
            history_lines = []
            for msg in reversed(conversation_history[-10:]):  # Last 10 messages, chronological order
                history_lines.append(f"{msg['role'].capitalize()}: {msg['content']}")
            history_section = "\n\nRecent conversation:\n" + "\n".join(history_lines)
            system_prompt += history_section

        # Add active order context hint if available
        active_order_id = state.get("active_order_id")
        if active_order_id:
            system_prompt += f"\n\nContext: The customer is currently discussing Order #{active_order_id}. If they refer to 'it', 'that order', or 'this order', they likely mean Order #{active_order_id}."

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

        # Call Groq with tools
        try:
            response = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=messages,
                tools=AVAILABLE_TOOLS,
                tool_choice="auto",
                temperature=0.0,
            )
        except Exception as err:
            log.append(f"  ERROR: Groq API failed: {err}")
            return {"error": f"Groq API failed: {err}", "execution_log": log}

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
        updated_active_order_id = state.get("active_order_id")
        updated_last_action = state.get("last_action")

        for tc in response_message.tool_calls:
            tool_name = tc.function.name
            try:
                tool_args = json.loads(tc.function.arguments)
            except Exception:
                tool_args = {"raw": tc.function.arguments}

            log.append(f"  tool requested: {tool_name}({tool_args})")

            # Dispatch through existing backend validation + auth + execution
            execution = _execute_tool_call(
                tool_name, tool_args, state["authenticated_customer_id"]
            )

            log.append(f"    validation: {'✅' if execution['validation_passed'] else '❌'}")
            if execution["authorization_passed"] is not None:
                log.append(f"    authorization: {'✅' if execution['authorization_passed'] else '❌'}")
            log.append(f"    executed: {'✅' if execution['backend_executed'] else '❌'}")

            tool_result = execution["tool_result"]

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
            if execution["backend_executed"] and tool_name in ("get_order_status", "issue_refund", "create_replacement_request"):
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

        # Update active order and last action in state
        if updated_active_order_id != state.get("active_order_id"):
            result["active_order_id"] = updated_active_order_id
        if updated_last_action != state.get("last_action"):
            result["last_action"] = updated_last_action

        # If we generated a final response for PENDING_APPROVAL, include it
        if final_response:
            result["final_response"] = final_response

        return result


def approval_check_node(state: SupportFlowState) -> dict:
    """Records that an approval is pending. Does NOT fake/bypass approval.

    In a real system this node would wait for the human decision.
    For this milestone it captures the state and lets the response
    generator inform the customer.

    Reuses: approval status already set by tool execution results.
    """
    with traced_span("supportflow.graph.approval_check") as span:
        log = list(state.get("execution_log", []))
        log.append("→ ENTERED: approval_check_node")
        log.append(f"  approval_status: {state.get('approval_status')}")

        if span:
            safe_set_attribute(span, "approval_status", state.get("approval_status", ""))

        return {"execution_log": log}


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
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            return {
                "final_response": "I'm sorry, I'm unable to process your request at this time.",
                "execution_log": log,
            }

        client = Groq(api_key=api_key)

        context_parts = [f"Customer message: {state['customer_message']}"]
        if state.get("classification"):
            context_parts.append(f"Classification: {state['classification']}")
        if state.get("error"):
            context_parts.append(f"Error: {state['error']}")

        try:
            response = client.chat.completions.create(
                model=GROQ_MODEL,
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
            )
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
    
    STAGE 12 FIX: Adds routing safeguard for implicit order references.
    If active_order_id exists and message appears to be an order-specific
    follow-up (even if misclassified as General Inquiry), route to tool execution.
    """
    if state.get("error"):
        return "error_node"

    classification = state.get("classification", {})
    category = (classification.get("category", "") or "").strip()
    category_lower = category.lower()
    
    # ROUTING SAFEGUARD: Detect implicit order-specific follow-ups
    # If we have active order context and the message looks like an order action,
    # route to tool execution even if classifier said "General Inquiry"
    active_order_id = state.get("active_order_id")
    message_lower = state.get("customer_message", "").lower()
    
    # Order-specific action keywords that indicate tool execution is needed
    ORDER_ACTION_KEYWORDS = [
        "status", "cancel", "refund", "replace", "track", "ship",
        "deliver", "return", "exchange", "modify", "update", "change"
    ]
    
    # Policy question indicators that should NOT be overridden
    POLICY_KEYWORDS = ["policy", "policies", "rule", "rules", "guideline", "guidelines"]
    
    # Debug logging
    import sys
    print(f"[DEBUG route_after_classify] active_order_id={active_order_id}, category={category}, message={state.get('customer_message')}", file=sys.stderr)
    
    if active_order_id and "general inquiry" in category_lower:
        # Don't override if asking about policy/rules (even with order context)
        is_policy_question = any(keyword in message_lower for keyword in POLICY_KEYWORDS)
        
        print(f"[DEBUG] is_policy_question={is_policy_question}", file=sys.stderr)
        
        if not is_policy_question:
            # Check if message contains order-specific action words
            has_order_action = any(keyword in message_lower for keyword in ORDER_ACTION_KEYWORDS)
            
            # Additional check: very short messages like "What's the status?" with active context
            is_short_contextual = len(message_lower.split()) <= 5 and has_order_action
            
            print(f"[DEBUG] has_order_action={has_order_action}, is_short_contextual={is_short_contextual}", file=sys.stderr)
            
            if has_order_action or is_short_contextual:
                # Override misclassification - this is likely an implicit order reference
                print(f"[DEBUG] SAFEGUARD TRIGGERED: Routing to tool_execution_node", file=sys.stderr)
                return "tool_execution_node"
    
    # Standard classification routing
    if any(kc.lower() in category_lower for kc in KNOWLEDGE_CATEGORIES):
        print(f"[DEBUG] Routing to retrieve_knowledge_node (knowledge category)", file=sys.stderr)
        return "retrieve_knowledge_node"

    # Default: assume action-oriented → tool execution
    print(f"[DEBUG] Routing to tool_execution_node (default)", file=sys.stderr)
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

def build_supportflow_graph():
    """Constructs and compiles the SupportFlow LangGraph workflow.

    Graph structure:
        START → classify_node
          → [route_after_classify]
             ├── retrieve_knowledge_node → generate_response_node → END
             ├── tool_execution_node
             │     → [route_after_tool]
             │        ├── tool_execution_node (loop, max 5)
             │        ├── approval_check_node → generate_response_node → END
             │        ├── error_node → END
             │        └── generate_response_node → END
             └── error_node → END
    """
    graph = StateGraph(SupportFlowState)

    # ── Add nodes ─────────────────────────────────────────────────
    graph.add_node("classify_node", classify_node)
    graph.add_node("retrieve_knowledge_node", retrieve_knowledge_node)
    graph.add_node("tool_execution_node", tool_execution_node)
    graph.add_node("approval_check_node", approval_check_node)
    graph.add_node("generate_response_node", generate_response_node)
    graph.add_node("error_node", error_node)

    # ── Add edges ─────────────────────────────────────────────────
    # START → classify
    graph.add_edge(START, "classify_node")

    # classify → conditional routing
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

    return graph.compile()


# Compiled graph singleton
_compiled_graph = None


def _get_graph():
    """Returns the compiled graph singleton."""
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_supportflow_graph()
    return _compiled_graph


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
    result = graph.invoke(initial_state)
    return result
