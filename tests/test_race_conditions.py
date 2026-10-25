"""
Regression tests for context ordering and concurrency bugs.

- Classifier context: get_recent_messages() returns newest-first, so the
  classifier must use the 3 most recent messages, not the 3 oldest.
- Refund race: concurrent refunds for one order must create exactly one
  refund Action (order row is locked with SELECT ... FOR UPDATE).
- Approval race: concurrent approvals of one request must resolve it
  exactly once (approval row is locked with SELECT ... FOR UPDATE).
"""

import concurrent.futures
import json
import os
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.approval import approve_request, create_approval_request
from app.llm import classify_ticket
from app.models import Action
from app.tools import issue_refund


def _thread_sessionmaker():
    """One independent connection per session, as each HTTP request gets."""
    engine = create_engine(os.environ["DATABASE_URL"], poolclass=NullPool)
    return sessionmaker(bind=engine)


def _run_concurrently(fn, n=10):
    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as executor:
        futures = [executor.submit(fn) for _ in range(n)]
        return [f.result() for f in futures]


# ══════════════════════════════════════════════════════════════════════
# Classifier context ordering
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestClassifierContextOrdering:
    def test_uses_three_most_recent_messages_in_chronological_order(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "test-key")

        # Newest-first, exactly as get_recent_messages() returns them
        history = [
            {"role": "user", "content": f"message-{i}", "created_at": ""}
            for i in range(9, -1, -1)
        ]

        msg = MagicMock()
        msg.content = json.dumps(
            {"category": "Order Issue", "priority": "LOW", "sentiment": "NEUTRAL"}
        )
        response = MagicMock()
        response.choices = [MagicMock(message=msg)]

        with patch("app.llm.Groq") as mock_groq_cls:
            client = MagicMock()
            client.chat.completions.create.return_value = response
            mock_groq_cls.return_value = client

            classify_ticket("Can I cancel it?", recent_messages=history)

            system_prompt = client.chat.completions.create.call_args.kwargs[
                "messages"
            ][0]["content"]

        assert "message-0" not in system_prompt, "oldest message leaked into context"
        assert "message-6" not in system_prompt
        positions = [system_prompt.index(f"message-{i}") for i in (7, 8, 9)]
        assert positions == sorted(positions), "context must be chronological"


# ══════════════════════════════════════════════════════════════════════
# Concurrent refunds
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.slow
class TestConcurrentRefunds:
    def test_concurrent_refunds_create_one_action(self, db, customer, orders):
        ThreadSession = _thread_sessionmaker()
        customer_id = customer.id

        def refund():
            sess = ThreadSession()
            try:
                return issue_refund(
                    order_id=1002,
                    amount=50.0,
                    authenticated_customer_id=customer_id,
                    db=sess,
                )
            finally:
                sess.close()

        results = _run_concurrently(refund)

        completed = [r for r in results if r.get("status") == "COMPLETED"]
        assert len(completed) == 1, f"expected 1 refund, got {len(completed)}: {results}"

        db.expire_all()
        refund_actions = (
            db.query(Action)
            .filter(Action.action_type == "REFUND", Action.reference_id == 1002)
            .count()
        )
        assert refund_actions == 1


# ══════════════════════════════════════════════════════════════════════
# Concurrent approvals
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.slow
class TestConcurrentApprovals:
    def test_concurrent_approvals_resolve_once(self, db, customer, orders):
        pending = create_approval_request(
            action_type="REFUND",
            reference_id=1006,
            customer_id=customer.id,
            amount=1100.0,
            db=db,
        )
        approval_id = pending["approval_id"]
        ThreadSession = _thread_sessionmaker()

        def approve():
            sess = ThreadSession()
            try:
                return approve_request(approval_id, resolved_by="admin", db=sess)
            finally:
                sess.close()

        results = _run_concurrently(approve)

        succeeded = [r for r in results if "error" not in r]
        assert len(succeeded) == 1, f"expected 1 approval, got {len(succeeded)}: {results}"
