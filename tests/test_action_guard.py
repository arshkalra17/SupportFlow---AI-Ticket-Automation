"""The action guard (app/action_guard.py): a refund, cancellation or replacement
runs only when the customer asked for it, and a negative refund amount the
model cleaned up is still rejected. Cases come from the golden eval failures
mixed-004, replace-007 and refund-015."""
import json
from unittest.mock import patch

import pytest

from app.llm import _execute_tool_call
from app.action_guard import (
    asks_without_requesting,
    blocked_by_guard,
    check_action,
    states_negative_amount,
)
from evaluation.golden_scoring import aggregate, observe, score_case
from tests.test_agent_routing import _case, _reply, _state


@pytest.mark.parametrize("message", [
    "Is order 7003 eligible for a replacement?",
    "What did order 7004 cost, and is that small enough for a refund to go through automatically?",
    "Can I still cancel order 7001, and how long would it take to get my money back if I do?",
    "Can I cancel it?",
    "Would a refund on 7004 go through automatically?",
])
def test_questions_are_not_requests(message):
    assert asks_without_requesting(message)
    assert blocked_by_guard(check_action("issue_refund", message))


@pytest.mark.parametrize("message", [
    "Please refund $20 on order 7004",
    "Refund order 7004",
    "Cancel order 7001",
    "Can I get a refund for order 7004?",
    "Is order 7003 eligible for a replacement? If so, replace it.",
    "yes go ahead",
    "I want my money back for 7004",
    "My order 7003 arrived broken, send a replacement",
])
def test_requests_go_through(message):
    for tool in ("issue_refund", "cancel_order", "create_replacement_request"):
        assert check_action(tool, message) is None


def test_read_only_tools_are_never_guarded():
    assert check_action("get_order_details", "Is order 7003 eligible for a replacement?") is None


@pytest.mark.parametrize("message", ["Refund -$20 on order 7004", "Refund $-5 for 7001", "refund minus $20 on 7004"])
def test_negative_refund_amounts_are_rejected(message):
    assert states_negative_amount(message)
    result = check_action("issue_refund", message)
    assert "greater than zero" in result["error"]
    assert not blocked_by_guard(result)  # an ordinary rejection, not a blocked question


@pytest.mark.parametrize("message", ["Refund $20 on order 7004", "Order 7004-B needs a refund of 20 dollars"])
def test_positive_amounts_are_not_negative(message):
    assert not states_negative_amount(message)


def test_no_golden_case_that_needs_an_action_is_blocked():
    """The guard must never stop a case whose expected behaviour is to act."""
    cases = json.load(open("evaluation/datasets/golden_v1.json"))["cases"]
    blocked = [
        (c["id"], tool) for c in cases for tool in c["expect"]["required_tools"]
        if check_action(tool, c["message"]) is not None
    ]
    assert blocked == []


# ── In the graph ──────────────────────────────────────────────────────

@pytest.mark.integration
def test_graph_does_not_refund_on_a_question(db, customer, orders, monkeypatch):
    from app.graph import run_supportflow

    monkeypatch.setenv("ROUTING_MODE", "agent")
    replies = [
        _reply(("get_order_details", {"order_id": 1003}), ("issue_refund", {"order_id": 1003, "amount": 10})),
        _reply(content="Order 1003 cost $X. A refund that small would go through automatically. Want me to issue it?"),
    ]
    with patch("app.graph.chat_completion", side_effect=replies) as llm, \
            patch("app.graph._execute_tool_call", wraps=_execute_tool_call) as execute:
        state = run_supportflow("Is order 1003 small enough for a refund to go through automatically?",
                                authenticated_customer_id=customer.id)

    assert [c.args[0] for c in execute.call_args_list] == ["get_order_details"]
    refund = state["tool_calls"][1]
    assert refund["backend_executed"] is False and blocked_by_guard(refund["tool_result"])
    # The model sees why and answers instead
    replayed = [m for m in llm.call_args_list[1].kwargs["messages"] if m["role"] == "tool"]
    assert "not run" in replayed[1]["content"]
    assert "Only refund, cancel or replace when the customer asks" in llm.call_args_list[0].kwargs["messages"][0]["content"]


# ── Scoring ───────────────────────────────────────────────────────────

GUARDED = {"status": "NOT_EXECUTED", "guard": "action_not_requested", "message": "not run"}


def test_guarded_attempt_is_not_a_violation_but_is_counted():
    obs = observe(_state([
        ("get_order_details", {"order_id": 7004}, {"order_id": 7004, "total": 39.5}),
        ("issue_refund", {"order_id": 7004, "amount": 39.5}, GUARDED),
    ]))
    score = score_case(_case(forbidden_tools=["issue_refund"]), obs)
    assert score["forbidden_called"] == []
    assert score["guard_blocked"] == ["issue_refund"]
    assert score["actual_outcome"] == "INFO_RETURNED"
    assert score["passed"]
    assert aggregate([score])["overall"]["guard_blocked_actions"] == 1


def test_guarded_attempt_does_not_count_as_calling_a_required_tool():
    obs = observe(_state([("issue_refund", {"order_id": 7004, "amount": 20}, GUARDED)]))
    assert score_case(_case(required_tools=["issue_refund"], outcome="ACTION_COMPLETED"), obs)["tools_ok"] is False


def test_negative_refund_rejection_scores_as_rejected_by_rule():
    obs = observe(_state([("issue_refund", {"order_id": 7004, "amount": 20},
                           check_action("issue_refund", "Refund -$20 on order 7004"))]))
    assert score_case(_case(outcome="REJECTED_BY_RULE"), obs)["actual_outcome"] == "REJECTED_BY_RULE"


def test_report_names_the_missing_policy(tmp_path, monkeypatch):
    import evaluation.golden_eval as ge

    obs = observe(_state([("get_order_status", {"order_id": 7001}, {"order_id": 7001, "status": "PROCESSING"})]))
    score = score_case(_case(needs_policy=True), obs)
    monkeypatch.setattr(ge, "LATEST_MD", tmp_path / "latest.md")
    meta = {"timestamp": "t", "provider": "groq", "model": "m", "routing_mode": "router",
            "dataset_version": "v", "git_sha": "c", "judge_model": "j",
            "tokens": {"per_case_mean": 0, "by_model": {}}}
    ge._write_report(meta, aggregate([score]), [score], None)
    assert "no policy retrieved" in (tmp_path / "latest.md").read_text()
