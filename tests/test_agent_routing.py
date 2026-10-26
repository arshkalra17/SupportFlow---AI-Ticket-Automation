"""ROUTING_MODE=agent: one tool loop with a read-only search_policy tool
instead of classify → route (app/graph.py), and how the golden eval scores it."""
import json
from unittest.mock import MagicMock, patch

import pytest

from app.llm import AGENT_TOOLS, AVAILABLE_TOOLS, POLICY_NOT_FOUND_MESSAGE, _execute_tool_call
from app.llm_client import LLMResult
from evaluation.golden_scoring import observe, score_case


def _reply(*calls, content=None):
    """An LLM turn: tool calls as (name, args) pairs, or a final answer."""
    message = MagicMock()
    message.content = content
    if calls:
        message.tool_calls = []
        for i, (name, args) in enumerate(calls):
            call = MagicMock()
            call.id = f"call_{i}"
            call.function.name = name
            call.function.arguments = json.dumps(args)
            message.tool_calls.append(call)
    else:
        message.tool_calls = None
    response = MagicMock()
    response.choices = [MagicMock(message=message)]
    return LLMResult(response=response, provider="groq", model="test")


def _tool_names(tools):
    return {t["function"]["name"] for t in tools}


# ── search_policy tool ────────────────────────────────────────────────

def test_agent_tools_add_only_search_policy():
    assert _tool_names(AGENT_TOOLS) - _tool_names(AVAILABLE_TOOLS) == {"search_policy"}


@pytest.mark.parametrize("args", [{}, {"query": ""}, {"query": "   "}, {"query": 42}, {"query": "x" * 501}])
def test_search_policy_rejects_bad_queries(args):
    execution = _execute_tool_call("search_policy", args, authenticated_customer_id=1)
    assert execution["validation_passed"] is False
    assert execution["backend_executed"] is False
    assert "Validation Error" in execution["tool_result"]["error"]


@pytest.mark.slow
def test_search_policy_returns_sections(seeded_kb, db):
    execution = _execute_tool_call("search_policy", {"query": "how long does express shipping take"}, 1)
    assert execution["backend_executed"] is True
    assert execution["tool_result"]["found"] is True
    titles = [s["title"] for s in execution["tool_result"]["sections"]]
    assert "Shipping & Delivery Guidelines" in titles
    assert execution["documents"][0]["content"]


@pytest.mark.slow
def test_search_policy_reports_nothing_found(seeded_kb, db):
    execution = _execute_tool_call("search_policy", {"query": "recipe for chocolate cake"}, 1)
    assert execution["tool_result"] == {"found": False, "message": POLICY_NOT_FOUND_MESSAGE}
    assert execution["documents"] == []


# ── Graph in agent mode ───────────────────────────────────────────────

def test_routing_mode_rejects_unknown_values(monkeypatch):
    from app.graph import routing_mode

    monkeypatch.setenv("ROUTING_MODE", "magic")
    with pytest.raises(ValueError):
        routing_mode()


def test_agent_graph_has_no_classify_step():
    from app.graph import build_supportflow_graph

    nodes = set(build_supportflow_graph(mode="agent").get_graph().nodes)
    assert "classify_node" not in nodes and "retrieve_knowledge_node" not in nodes
    assert "tool_execution_node" in nodes
    assert {"classify_node", "retrieve_knowledge_node"} <= set(build_supportflow_graph(mode="router").get_graph().nodes)


@pytest.mark.integration
def test_agent_reads_policy_and_order_in_one_run(db, seeded_kb, customer, orders, monkeypatch):
    """A mixed question: the agent checks the order and the policy, never classifies."""
    from app.graph import run_supportflow

    monkeypatch.setenv("ROUTING_MODE", "agent")
    replies = [
        _reply(("get_order_status", {"order_id": 1003}), ("search_policy", {"query": "refund after cancelling"})),
        _reply(content="Order 1003 is PROCESSING, so it can be cancelled; refunds take 5-7 business days."),
    ]
    with patch("app.graph.classify_ticket") as classify, \
            patch("app.graph.chat_completion", side_effect=replies) as llm:
        state = run_supportflow("Can I cancel order 1003 and when is the refund?", authenticated_customer_id=customer.id)

    classify.assert_not_called()
    assert _tool_names(llm.call_args_list[0].kwargs["tools"]) == _tool_names(AGENT_TOOLS)
    assert "search_policy" in llm.call_args_list[0].kwargs["messages"][0]["content"]
    assert [tc["tool_name"] for tc in state["tool_calls"]] == ["get_order_status", "search_policy"]
    assert state["retrieved_documents"], "policy the agent read is kept for the audit trail"
    assert state["error"] is None

    obs = observe(state)
    assert obs["route"] == "tools"  # an order tool ran
    assert obs["policy_used"] is True


@pytest.mark.integration
def test_router_mode_still_offers_only_the_original_tools(db, customer, orders, monkeypatch):
    from app.graph import run_supportflow

    monkeypatch.setenv("ROUTING_MODE", "router")
    with patch("app.graph.classify_ticket", return_value={
        "category": "Order Issue", "priority": "LOW", "sentiment": "NEUTRAL",
    }), patch("app.graph.chat_completion", side_effect=[_reply(content="Which order?")]) as llm:
        run_supportflow("Please cancel my order", authenticated_customer_id=customer.id)

    assert _tool_names(llm.call_args_list[0].kwargs["tools"]) == _tool_names(AVAILABLE_TOOLS)


# ── Golden scoring is mode-neutral ────────────────────────────────────

def _case(**expect):
    base = {"route": "any", "required_tools": [], "tool_args": {}, "forbidden_tools": [],
            "outcome": "INFO_RETURNED", "also_acceptable": [], "kb_doc": None,
            "must_not_say": [], "judge": "x"}
    base.update(expect)
    return {"id": "c", "category": "t", "difficulty": "easy", "expect": base}


def _state(tool_calls=(), retrieved=None, tool_iterations=1):
    return {
        "tool_calls": [{"tool_name": n, "tool_args": a, "tool_result": r} for n, a, r in tool_calls],
        "retrieved_documents": retrieved,
        "final_response": "answer",
        "tool_iterations": tool_iterations,
        "classification": None,
        "error": None,
    }


SECTION = {"title": "Shipping & Delivery Guidelines", "content": "3-5 business days"}


def test_agent_search_only_scores_as_a_policy_answer():
    obs = observe(_state([("search_policy", {"query": "q"}, {"found": True})], retrieved=[SECTION]))
    score = score_case(_case(route="rag", outcome="ANSWERED_FROM_POLICY"), obs)
    assert obs["route"] == "rag"
    assert score["actual_outcome"] == "ANSWERED_FROM_POLICY"
    assert score["passed"]


def test_search_policy_never_decides_the_outcome():
    obs = observe(_state(
        [("search_policy", {"query": "q"}, {"found": True}),
         ("get_order_status", {"order_id": 7001}, {"order_id": 7001, "status": "PROCESSING"})],
        retrieved=[SECTION],
    ))
    assert obs["route"] == "tools"
    assert score_case(_case(needs_policy=True), obs)["actual_outcome"] == "INFO_RETURNED"


def test_mixed_case_fails_without_policy_text():
    obs = observe(_state([("get_order_status", {"order_id": 7001}, {"order_id": 7001, "status": "PROCESSING"})]))
    score = score_case(_case(needs_policy=True), obs)
    assert score["policy_ok"] is False
    assert not score["passed"]


def test_mixed_case_fails_on_policy_alone():
    """The router's RAG branch reads policy but never sees the order."""
    obs = observe(_state(retrieved=[SECTION], tool_iterations=0))
    score = score_case(_case(needs_policy=True), obs)
    assert score["policy_ok"] is True
    assert score["outcome_ok"] is False
    assert not score["passed"]


def test_agent_answer_without_tools_is_a_tools_route():
    obs = observe(_state())
    assert obs["route"] == "tools"
    assert obs["policy_used"] is False


def test_baselines_are_kept_per_routing_mode():
    from evaluation.golden_eval import baseline_path, progress_path

    assert baseline_path("groq", "smoke").name == "golden_baseline_smoke.json"
    assert baseline_path("groq", "smoke", "agent").name == "golden_baseline_agent_smoke.json"
    assert progress_path("groq", "smoke", "agent").name == "golden_progress_agent_smoke.jsonl"
