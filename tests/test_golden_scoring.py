"""
Unit tests for golden-set scoring, the judge wrapper and the regression gate.
No LLM and no database: pipeline states and judge replies are faked.
"""

import json
from unittest.mock import MagicMock

import pytest

from evaluation.golden_scoring import (
    aggregate,
    compare_to_baseline,
    derive_outcome,
    observe,
    score_case,
)
from evaluation.judge import judge_case

pytestmark = pytest.mark.unit


def _case(**expect):
    base = {
        "route": "tools", "required_tools": [], "tool_args": {}, "forbidden_tools": [],
        "outcome": "INFO_RETURNED", "also_acceptable": [], "kb_doc": None,
        "must_not_say": [], "judge": "rubric",
    }
    base.update(expect)
    return {"id": "t-1", "category": "test", "difficulty": "easy", "customer_id": 1,
            "history": [], "context": {}, "message": "msg", "expect": base}


def _state(tool_calls=(), response="ok", retrieved=None, error=None):
    return {
        "tool_calls": [
            {"tool_name": n, "tool_args": a, "tool_result": r} for n, a, r in tool_calls
        ],
        "retrieved_documents": retrieved,
        "final_response": response,
        "error": error,
        "classification": {"category": "Order Issue"},
    }


# ── observe / derive_outcome ──────────────────────────────────────────

def test_rag_route_and_relevance():
    obs = observe(_state(retrieved=[{"title": "Return and Refund Policy"}], response="30 days."))
    assert obs["route"] == "rag" and obs["rag_relevant"]
    assert derive_outcome(obs) == "ANSWERED_FROM_POLICY"


def test_rag_refusal_is_out_of_scope():
    obs = observe(_state(retrieved=[], response="I could not find any relevant company policy to answer."))
    assert derive_outcome(obs) == "REFUSED_OUT_OF_SCOPE"


def test_state_changing_outcome_wins_over_lookups():
    obs = observe(_state(tool_calls=[
        ("get_order_status", {"order_id": 1}, {"status": "DELIVERED"}),
        ("issue_refund", {"order_id": 1, "amount": 5}, {"status": "PENDING_APPROVAL"}),
    ]))
    assert derive_outcome(obs) == "PENDING_APPROVAL"


def test_blocked_dominates_mixed_results():
    obs = observe(_state(tool_calls=[
        ("issue_refund", {"order_id": 1, "amount": 5}, {"status": "COMPLETED"}),
        ("issue_refund", {"order_id": 2, "amount": 5}, {"error": "Unauthorized: no access"}),
    ]))
    assert derive_outcome(obs) == "BLOCKED_UNAUTHORIZED"


def test_no_tools_no_rag_has_no_derivable_outcome():
    assert derive_outcome(observe(_state(response="Which order?"))) is None


# ── score_case ────────────────────────────────────────────────────────

def test_correct_tool_call_passes():
    case = _case(required_tools=["issue_refund"],
                 tool_args={"issue_refund": {"order_id": 7003, "amount": 1200.0}},
                 outcome="PENDING_APPROVAL")
    obs = observe(_state(tool_calls=[
        ("issue_refund", {"order_id": 7003, "amount": 1200}, {"status": "PENDING_APPROVAL"}),
    ]))
    s = score_case(case, obs)
    assert s["passed"] and s["args_ok"] and s["outcome_ok"]


def test_wrong_argument_fails():
    case = _case(required_tools=["get_order_status"], tool_args={"get_order_status": {"order_id": 7003}})
    obs = observe(_state(tool_calls=[("get_order_status", {"order_id": 7002}, {"status": "SHIPPED"})]))
    s = score_case(case, obs)
    assert s["args_ok"] is False and not s["passed"]


def test_forbidden_tool_is_a_safety_violation():
    case = _case(forbidden_tools=["cancel_order"])
    obs = observe(_state(tool_calls=[("cancel_order", {"order_id": 1}, {"status": "COMPLETED"})]))
    s = score_case(case, obs)
    assert s["forbidden_called"] == ["cancel_order"] and not s["passed"]


def test_unsafe_phrase_is_case_insensitive():
    case = _case(must_not_say=["refund has been issued"], outcome="PENDING_APPROVAL")
    obs = observe(_state(response="Good news: your Refund Has Been Issued!"))
    s = score_case(case, obs)
    assert s["unsafe_phrases"] == ["refund has been issued"] and not s["passed"]


def test_also_acceptable_outcome_passes():
    case = _case(outcome="PENDING_APPROVAL", also_acceptable=["REFUSED_OUT_OF_SCOPE"], route="any")
    obs = observe(_state(retrieved=[], response="I could not find any relevant company policy."))
    assert score_case(case, obs)["outcome_ok"] is True


def test_clarification_without_tools_is_left_to_judge():
    case = _case(outcome="CLARIFICATION")
    s = score_case(case, observe(_state(response="Which order do you mean?")))
    assert s["outcome_ok"] is None and s["passed"]


def test_expected_action_but_nothing_attempted_fails():
    case = _case(outcome="ACTION_COMPLETED")
    assert score_case(case, observe(_state(response="Sure!")))["outcome_ok"] is False


def test_judge_failure_fails_case():
    s = score_case(_case(), observe(_state()), judge={"score": 2, "pass": False, "reason": "made up a date"})
    assert not s["passed"] and s["judge_reason"] == "made up a date"


# ── aggregate / gate ──────────────────────────────────────────────────

def _scores(passed_flags, forbidden=0):
    out = []
    for i, p in enumerate(passed_flags):
        out.append({
            "id": f"c{i}", "category": "cat", "difficulty": "easy", "passed": p,
            "route_ok": True, "tools_ok": None, "args_ok": None, "outcome_ok": p,
            "judge_pass": p, "forbidden_called": ["cancel_order"] if i < forbidden else [],
            "unsafe_phrases": [],
        })
    return out


def test_aggregate_rates_ignore_skipped_checks():
    m = aggregate(_scores([True, True, False, True]))["overall"]
    assert m["pass_rate"] == 0.75 and m["tool_recall"] is None


def test_gate_flags_drop_beyond_tolerance():
    base = aggregate(_scores([True] * 10))
    cur = aggregate(_scores([True] * 9 + [False]))
    assert any("pass_rate" in r for r in compare_to_baseline(cur, base, tolerance=0.03))
    assert compare_to_baseline(cur, base, tolerance=0.15) == []


def test_gate_flags_any_new_safety_violation():
    base = aggregate(_scores([True] * 10))
    cur = aggregate(_scores([True] * 10, forbidden=1))
    assert compare_to_baseline(cur, base, tolerance=1.0) == ["forbidden_tool_violations: 0 -> 1"]


# ── stratified sample ─────────────────────────────────────────────────

def test_stratified_sample_covers_every_category():
    from evaluation.golden import load_golden
    from evaluation.golden_eval import stratified_sample

    cases = load_golden()["cases"]
    categories = {c["category"] for c in cases}
    sample = stratified_sample(cases, 2 * len(categories))
    assert len(sample) == 2 * len(categories)
    assert {c["category"] for c in sample} == categories
    assert sample == stratified_sample(cases, 2 * len(categories))  # deterministic


# ── judge wrapper ─────────────────────────────────────────────────────

def test_judge_pass_is_derived_from_score():
    client = MagicMock()
    client.chat.completions.create.return_value.choices = [
        MagicMock(message=MagicMock(content=json.dumps({"score": 3, "pass": True, "reason": "partial"})))
    ]
    verdict = judge_case(_case(), observe(_state()), client=client)
    assert verdict == {"score": 3, "pass": False, "reason": "partial"}


def test_judge_sees_policy_text():
    from evaluation.judge import _render

    obs = observe(_state(retrieved=[{"title": "Return and Refund Policy",
                                     "content": "Returns are accepted within 30 days of delivery."}]))
    assert "within 30 days of delivery" in _render(_case(), obs)


# ── quota handling ────────────────────────────────────────────────────

def test_daily_quota_is_told_apart_from_per_minute_limit():
    from evaluation.golden_eval import _is_daily_quota, _is_rate_limited

    tpd = ("Error code: 429 - Rate limit reached for model `openai/gpt-oss-120b` "
           "on tokens per day (TPD): Limit 200000, Used 199276")
    tpm = "Error code: 429 - Rate limit reached on tokens per minute (TPM): Limit 8000"
    assert _is_daily_quota(tpd) and _is_rate_limited(tpd)
    assert not _is_daily_quota(tpm) and _is_rate_limited(tpm)


def test_baselines_are_separate_per_provider_and_tier():
    from evaluation.golden_eval import baseline_path

    names = {baseline_path(p, t).name for p in ("groq", "gemini") for t in ("full", "smoke")}
    assert names == {"golden_baseline.json", "golden_baseline_smoke.json",
                     "golden_baseline_gemini.json", "golden_baseline_gemini_smoke.json"}


def test_progress_files_are_separate_per_provider_and_tier():
    from evaluation.golden_eval import progress_path

    assert progress_path("groq", "full").name == "golden_progress.jsonl"
    assert progress_path("gemini", "smoke").name == "golden_progress_gemini_smoke.jsonl"
