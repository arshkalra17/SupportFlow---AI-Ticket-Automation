"""
Tests for execute-on-approve (app/actions.py).

Approving a request runs the linked action through the same executor as
auto-approved refunds, and re-checks the refund rules at execution time.
If the rules no longer hold, the approval is recorded but the action is
marked FAILED and nothing is executed.
"""

import pytest

from app.actions import run_action
from app.approval import approve_request, create_approval_request
from app.models import Action, Order
from app.tools import issue_refund


def _pending_refund(db, customer, order_id=1006, amount=1100.0):
    return create_approval_request(
        action_type="REFUND",
        reference_id=order_id,
        customer_id=customer.id,
        amount=amount,
        db=db,
    )


def _action(db, action_id):
    db.expire_all()
    return db.query(Action).filter_by(id=action_id).first()


@pytest.mark.integration
class TestApproveExecutesAction:
    def test_approval_executes_valid_refund(self, db, customer, orders):
        pending = _pending_refund(db, customer)

        result = approve_request(pending["approval_id"], resolved_by="admin", db=db)

        assert result["executed"] is True
        assert result["execution_error"] is None
        assert _action(db, pending["action_id"]).status == "COMPLETED"

    def test_order_total_reduced_after_request_fails_execution(self, db, customer, orders):
        pending = _pending_refund(db, customer)
        order = db.query(Order).filter_by(id=1006).first()
        order.total = 500.0  # e.g. partial return processed in the meantime
        db.commit()

        result = approve_request(pending["approval_id"], resolved_by="admin", db=db)

        assert result["approval_status"] == "APPROVED"  # human decision kept
        assert result["executed"] is False
        assert "exceeds the total" in result["execution_error"]
        assert _action(db, pending["action_id"]).status == "FAILED"

    def test_refund_completed_elsewhere_fails_execution(self, db, customer, orders):
        pending = _pending_refund(db, customer)
        db.add(Action(
            action_type="REFUND", reference_id=1006, customer_id=customer.id,
            amount=100.0, status="COMPLETED",
        ))
        db.commit()

        result = approve_request(pending["approval_id"], resolved_by="admin", db=db)

        assert result["executed"] is False
        assert "already refunded" in result["execution_error"]
        assert _action(db, pending["action_id"]).status == "FAILED"

    def test_order_ownership_changed_fails_execution(self, db, customer, customer2, orders):
        pending = _pending_refund(db, customer)
        order = db.query(Order).filter_by(id=1006).first()
        order.customer_id = customer2.id
        db.commit()

        result = approve_request(pending["approval_id"], resolved_by="admin", db=db)

        assert result["executed"] is False
        assert "not owned" in result["execution_error"]

    def test_unknown_action_type_fails_closed(self, db, customer, orders):
        pending = create_approval_request(
            action_type="REPLACEMENT_REQUEST",
            reference_id=1002,
            customer_id=customer.id,
            db=db,
        )

        result = approve_request(pending["approval_id"], resolved_by="admin", db=db)

        assert result["executed"] is False
        assert "No executor" in result["execution_error"]
        assert _action(db, pending["action_id"]).status == "FAILED"

    def test_failed_refund_does_not_block_a_new_request(self, db, customer, orders):
        pending = _pending_refund(db, customer)
        order = db.query(Order).filter_by(id=1006).first()
        order.total = 500.0
        db.commit()
        approve_request(pending["approval_id"], resolved_by="admin", db=db)

        retry = issue_refund(order_id=1006, amount=400.0, authenticated_customer_id=customer.id, db=db)

        assert retry.get("status") == "COMPLETED"


@pytest.mark.integration
class TestAutoRefundUsesExecutor:
    def test_auto_refund_completes_through_executor(self, db, customer, orders):
        result = issue_refund(order_id=1002, amount=50.0, authenticated_customer_id=customer.id, db=db)

        assert result["status"] == "COMPLETED"
        assert _action(db, result["action_id"]).status == "COMPLETED"

    def test_run_action_does_not_commit(self, db, customer, orders):
        action = Action(
            action_type="REFUND", reference_id=1002, customer_id=customer.id,
            amount=10.0, status="PENDING",
        )
        db.add(action)
        db.flush()

        assert run_action(action, db) is None
        db.rollback()

        assert db.query(Action).filter_by(reference_id=1002).count() == 0


@pytest.mark.integration
def test_approve_api_reports_execution_failure(client, db, customer, orders):
    from app.auth import create_access_token, hash_password
    from app.models import Customer

    admin = Customer(email="admin@example.com", password_hash=hash_password("x"), is_admin=1)
    db.add(admin)
    db.commit()
    headers = {"Authorization": f"Bearer {create_access_token(customer_id=admin.id)}"}

    pending = _pending_refund(db, customer)
    order = db.query(Order).filter_by(id=1006).first()
    order.total = 500.0
    db.commit()

    response = client.post(f"/approvals/{pending['approval_id']}/approve", headers=headers, json={})

    assert response.status_code == 200
    body = response.json()
    assert body["approval_status"] == "APPROVED"
    assert body["action_status"] == "FAILED"
    assert body["executed"] is False
    assert "exceeds the total" in body["execution_error"]
