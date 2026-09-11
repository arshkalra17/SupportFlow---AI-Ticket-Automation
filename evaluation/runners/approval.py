"""
Runner — Metric 6: Approval-routing accuracy.

Calls issue_refund() directly from app.tools for each dataset case.
Zero Groq calls.  GROQ_API_KEY is not required.

Each dataset case specifies:
  - order_id / order_owner_id / requesting_customer_id
  - amount
  - expected   AUTO_EXECUTE | HUMAN_APPROVAL | REJECT

Routing labels are derived from issue_refund() return values:
  status == "COMPLETED"        → AUTO_EXECUTE
  status == "PENDING_APPROVAL" → HUMAN_APPROVAL
  "error" key present          → REJECT

Important: each case runs against a *fresh* set of eval orders so there
are no duplicate-refund false positives across cases.  The runner rebuilds
the eval DB before the approval suite runs (orders only; no schema changes).
The caller (run.py) has already called db_setup.setup_eval_db() before
invoking this runner, so orders exist.  Within this runner we remove any
Action/Approval rows created by earlier cases before each call to keep
the duplicate-prevention check from blocking legitimate test cases.
"""

from __future__ import annotations

import json
import os

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from evaluation.config import DATASETS_DIR, EVAL_DATABASE_URL


def _clean_refund_actions(session) -> None:
    """Remove all Action/Approval rows so duplicate-prevention never blocks a case."""
    session.execute(text("TRUNCATE TABLE approvals, actions RESTART IDENTITY CASCADE"))
    session.commit()


def _classify_outcome(result: dict) -> str:
    """Maps issue_refund() return dict to AUTO_EXECUTE / HUMAN_APPROVAL / REJECT."""
    if result.get("status") == "COMPLETED":
        return "AUTO_EXECUTE"
    if result.get("status") == "PENDING_APPROVAL":
        return "HUMAN_APPROVAL"
    # Any error — authorization failure, validation failure, not found, etc.
    return "REJECT"


def run() -> list[dict]:
    """
    Evaluates approval routing on the approval_routing dataset.

    Returns:
        List of result dicts:
            {
              "id":          str,
              "description": str,
              "order_id":    int,
              "amount":      float,
              "requesting_customer_id": int,
              "expected":    str,
              "actual":      str,
              "correct":     bool,
              "raw_result":  dict,
            }
    """
    os.environ["DATABASE_URL"] = EVAL_DATABASE_URL

    from app.tools import issue_refund

    # A dedicated session for clearing Action/Approval rows between cases.
    engine  = create_engine(EVAL_DATABASE_URL)
    Session = sessionmaker(bind=engine)
    cleaner = Session()

    dataset_path = DATASETS_DIR / "approval_routing.json"
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))

    results: list[dict] = []

    for case in dataset:
        case_id      = case["id"]
        order_id     = case["order_id"]
        amount       = case["amount"]
        customer_id  = case["requesting_customer_id"]
        expected     = case["expected"]

        # Clear previous refund actions so duplicate-prevention never fires
        # on a second case touching the same order.
        _clean_refund_actions(cleaner)

        raw_result = issue_refund(
            order_id=order_id,
            amount=amount,
            authenticated_customer_id=customer_id,
        )

        actual  = _classify_outcome(raw_result)
        correct = (actual == expected)
        mark    = "✓" if correct else f"✗ (got {actual!r})"

        print(
            f"  [appr] {case_id}: amount=${amount} "
            f"expected={expected!r} actual={actual!r} {mark}"
        )

        results.append({
            "id":                     case_id,
            "description":            case["description"],
            "order_id":               order_id,
            "amount":                 amount,
            "requesting_customer_id": customer_id,
            "expected":               expected,
            "actual":                 actual,
            "correct":                correct,
            "raw_result":             raw_result,
        })

    cleaner.close()

    correct_count = sum(1 for r in results if r["correct"])
    print(
        f"  [appr] done — {len(results)} cases, "
        f"{correct_count}/{len(results)} correct, 0 Groq calls"
    )
    return results
