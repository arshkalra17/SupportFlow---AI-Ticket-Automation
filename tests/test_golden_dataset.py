"""
Integrity checks for the golden evaluation dataset (evaluation/datasets/golden_v1.json).

Two layers:
  1. Structure (unit): ids, enums, tool names and argument names, references
     to fixture orders/tickets/customers, knowledge-base titles.
  2. Label audit (integration): for every case that names one concrete tool
     call, run that call through the production dispatch layer against the
     seeded fixtures and check the backend agrees with the labelled outcome.
     This catches labels that drift from the business rules.
"""

import pytest

from app.kb import kb_titles
from app.llm import AVAILABLE_TOOLS
from evaluation.golden import (
    STATE_CHANGING_TOOLS,
    history_newest_first,
    load_golden,
    outcome_of_tool_result,
    seed_fixtures,
)

GOLDEN = load_golden()
CASES = GOLDEN["cases"]
FIXTURES = GOLDEN["fixtures"]
OUTCOMES = set(GOLDEN["outcomes"])
TOOL_PARAMS = {
    t["function"]["name"]: set(t["function"]["parameters"].get("properties", {}))
    for t in AVAILABLE_TOOLS
}
KB_TITLES = set(kb_titles())
CUSTOMER_IDS = {c["id"] for c in FIXTURES["customers"]}
ORDER_IDS = {o["id"] for o in FIXTURES["orders"]}
TICKET_IDS = {t["id"] for t in FIXTURES["tickets"]}
# IDs that cases deliberately reference to test "not found"
MISSING_IDS = {9999, 5555}


def _ids(case):
    return pytest.param(case, id=case["id"])


# ══════════════════════════════════════════════════════════════════════
# Structure
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestGoldenStructure:
    def test_size_and_unique_ids(self):
        ids = [c["id"] for c in CASES]
        assert len(ids) >= 150
        assert len(ids) == len(set(ids)), "duplicate case ids"

    def test_every_category_has_enough_cases(self):
        from collections import Counter
        counts = Counter(c["category"] for c in CASES)
        assert all(n >= 6 for n in counts.values()), counts

    @pytest.mark.parametrize("case", [_ids(c) for c in CASES])
    def test_case_is_well_formed(self, case):
        exp = case["expect"]
        assert case["message"].strip()
        assert case["rationale"].strip()
        assert exp["judge"].strip()
        assert case["difficulty"] in {"easy", "medium", "hard"}
        assert case["customer_id"] in CUSTOMER_IDS
        assert exp["route"] in {"rag", "tools", "any"}
        assert exp["outcome"] in OUTCOMES
        assert set(exp["also_acceptable"]) <= OUTCOMES
        assert exp["outcome"] not in exp["also_acceptable"]
        assert all(m["role"] in {"user", "assistant"} for m in case["history"])

    @pytest.mark.parametrize("case", [_ids(c) for c in CASES])
    def test_tools_and_args_are_real(self, case):
        exp = case["expect"]
        tools = set(exp["required_tools"]) | set(exp["forbidden_tools"]) | set(exp["tool_args"])
        assert tools <= set(TOOL_PARAMS), tools - set(TOOL_PARAMS)
        assert not set(exp["required_tools"]) & set(exp["forbidden_tools"])
        for tool, args in exp["tool_args"].items():
            assert tool in exp["required_tools"], f"args given for non-required tool {tool}"
            assert set(args) <= TOOL_PARAMS[tool], f"unknown args for {tool}: {args}"

    @pytest.mark.parametrize("case", [_ids(c) for c in CASES])
    def test_references_exist_in_fixtures(self, case):
        for args in case["expect"]["tool_args"].values():
            if "order_id" in args:
                assert args["order_id"] in ORDER_IDS | MISSING_IDS
            if "ticket_id" in args:
                assert args["ticket_id"] in TICKET_IDS | MISSING_IDS
        ctx = case["context"]
        if ctx.get("active_order_id") is not None:
            assert ctx["active_order_id"] in ORDER_IDS
        if ctx.get("active_ticket_id") is not None:
            assert ctx["active_ticket_id"] in TICKET_IDS

    @pytest.mark.parametrize("case", [_ids(c) for c in CASES])
    def test_rag_cases_name_a_real_document(self, case):
        exp = case["expect"]
        if exp["route"] == "rag" or exp["outcome"] == "ANSWERED_FROM_POLICY":
            assert exp["kb_doc"] in KB_TITLES
        if exp["outcome"] in {"ANSWERED_FROM_POLICY", "INFO_RETURNED", "REFUSED_OUT_OF_SCOPE"} \
                and not exp["also_acceptable"]:
            assert STATE_CHANGING_TOOLS <= set(exp["forbidden_tools"]) | set(exp["required_tools"]), \
                "read-only case must forbid state-changing tools"

    def test_history_is_converted_newest_first(self):
        case = next(c for c in CASES if len(c["history"]) >= 2)
        converted = history_newest_first(case)
        assert converted[0]["content"] == case["history"][-1]["content"]


# ══════════════════════════════════════════════════════════════════════
# Label audit against the real backend
# ══════════════════════════════════════════════════════════════════════

AUDITABLE = [
    c for c in CASES
    if len(c["expect"]["required_tools"]) == 1
    and c["expect"]["required_tools"][0] in c["expect"]["tool_args"]
]


@pytest.mark.integration
class TestGoldenLabelsMatchBackend:
    def test_enough_cases_are_auditable(self):
        assert len(AUDITABLE) >= 80

    @pytest.mark.parametrize("case", [_ids(c) for c in AUDITABLE])
    def test_backend_agrees_with_label(self, case, db):
        from app.llm import _execute_tool_call

        seed_fixtures(db, FIXTURES)
        tool = case["expect"]["required_tools"][0]
        execution = _execute_tool_call(tool, case["expect"]["tool_args"][tool], case["customer_id"])
        actual = outcome_of_tool_result(tool, execution["tool_result"])

        accepted = {case["expect"]["outcome"], *case["expect"]["also_acceptable"]}
        assert actual in accepted, (
            f"{case['id']}: backend says {actual}, label says {sorted(accepted)}; "
            f"result={execution['tool_result']}"
        )
