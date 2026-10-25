"""
Golden dataset helpers: load evaluation/datasets/golden_v1.json and seed
its fixtures into a database.

The golden set is self-contained: every case runs against the same small
world (customers, orders, tickets, prior actions) defined in its
"fixtures" block, re-seeded before each case so state-changing cases
cannot affect each other.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

GOLDEN_PATH = Path(__file__).parent / "datasets" / "golden_v1.json"

STATE_CHANGING_TOOLS = {"cancel_order", "issue_refund", "create_replacement_request"}

# Children first, so TRUNCATE ... CASCADE never has to chase foreign keys.
_TABLES = [
    "messages", "conversation_contexts", "conversations",
    "idempotency_records", "approvals", "actions", "replacement_requests",
    "orders", "tickets", "customers",
]


def load_golden(path: Path = GOLDEN_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def seed_fixtures(db: Session, fixtures: dict) -> None:
    """Replaces all app rows (except knowledge documents) with the fixtures."""
    from app.models import Action, Customer, Order, ReplacementRequest, Ticket

    for table in _TABLES:
        db.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))

    for c in fixtures["customers"]:
        db.add(Customer(id=c["id"], email=c["email"], password_hash="golden-fixture"))
    db.flush()
    for o in fixtures["orders"]:
        db.add(Order(**o))
    for t in fixtures["tickets"]:
        db.add(Ticket(**t))
    for a in fixtures["actions"]:
        db.add(Action(**a))
    for r in fixtures["replacement_requests"]:
        db.add(ReplacementRequest(**r))
    db.commit()


def history_newest_first(case: dict) -> list[dict]:
    """The dataset stores history chronologically; run_supportflow expects
    it newest-first, as get_recent_messages() returns it."""
    return [
        {"role": m["role"], "content": m["content"], "created_at": ""}
        for m in reversed(case["history"])
    ]


def outcome_of_tool_result(tool_name: str, result: dict) -> str:
    """Maps a backend tool result onto the golden outcome vocabulary."""
    if not isinstance(result, dict):
        return "INFO_RETURNED"
    status = result.get("status")
    error = (result.get("error") or "").lower()
    if error:
        if "not found" in error:
            return "NOT_FOUND"
        if "unauthorized" in error or "permission" in error:
            return "BLOCKED_UNAUTHORIZED"
        return "REJECTED_BY_RULE"
    if status == "PENDING_APPROVAL":
        return "PENDING_APPROVAL"
    if status == "ALREADY_CANCELLED":
        return "ALREADY_DONE"
    if tool_name in STATE_CHANGING_TOOLS:
        return "ACTION_COMPLETED"
    return "INFO_RETURNED"
