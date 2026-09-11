"""
Stage 8 — Tool validation and execution tests (deterministic, no Groq).

Covers:
- Argument validation for get_order_status, create_replacement_request, issue_refund
- Authorization (ownership enforcement)
- Business rules (eligibility, duplicate prevention)
- Refund threshold (auto vs. PENDING_APPROVAL)
- Approval state transitions (approve_request, reject_request)
- _execute_tool_call dispatch layer
"""

import pytest
from app.tools import (
    validate_get_order_status_args,
    authorize_and_get_order_status,
    validate_create_replacement_request_args,
    create_replacement_request,
    validate_issue_refund_args,
    issue_refund,
    REFUND_AUTO_APPROVAL_THRESHOLD,
    REPLACEMENT_ELIGIBLE_STATUSES,
)
from app.approval import (
    create_approval_request,
    approve_request,
    reject_request,
)
from app.llm import _execute_tool_call
from app.models import Action, Approval, ReplacementRequest


# ══════════════════════════════════════════════════════════════════════
# get_order_status — argument validation
# ══════════════════════════════════════════════════════════════════════

class TestGetOrderStatusValidation:
    def test_valid_order_id(self):
        ok, args, err = validate_get_order_status_args({"order_id": 1001})
        assert ok is True
        assert args.order_id == 1001
        assert err is None

    def test_negative_order_id_rejected(self):
        ok, args, err = validate_get_order_status_args({"order_id": -1})
        assert ok is False
        assert err is not None

    def test_zero_order_id_rejected(self):
        ok, args, err = validate_get_order_status_args({"order_id": 0})
        assert ok is False

    def test_boolean_order_id_rejected(self):
        ok, args, err = validate_get_order_status_args({"order_id": True})
        assert ok is False
        assert "boolean" in err.lower()

    def test_string_order_id_rejected(self):
        ok, args, err = validate_get_order_status_args({"order_id": "abc"})
        assert ok is False

    def test_missing_order_id_rejected(self):
        ok, args, err = validate_get_order_status_args({})
        assert ok is False

    def test_non_dict_args_rejected(self):
        ok, args, err = validate_get_order_status_args("not-a-dict")
        assert ok is False


# ══════════════════════════════════════════════════════════════════════
# get_order_status — authorization + execution
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestGetOrderStatusExecution:
    def test_owner_gets_order_status(self, db, customer, orders):
        auth, err, result = authorize_and_get_order_status(
            authenticated_customer_id=customer.id,
            order_id=1001,
            db=db,
        )
        assert auth is True
        assert err is None
        assert result["order_id"] == 1001
        assert result["status"] == "SHIPPED"

    def test_non_owner_blocked(self, db, customer, orders):
        # Order 1004 belongs to customer2
        auth, err, result = authorize_and_get_order_status(
            authenticated_customer_id=customer.id,
            order_id=1004,
            db=db,
        )
        assert auth is False
        assert result is None
        assert "unauthorized" in err.lower()

    def test_nonexistent_order_returns_not_found(self, db, customer, orders):
        auth, err, result = authorize_and_get_order_status(
            authenticated_customer_id=customer.id,
            order_id=9999,
            db=db,
        )
        # Not-found is not an authorization failure
        assert auth is True
        assert "not found" in result["error"].lower()


# ══════════════════════════════════════════════════════════════════════
# create_replacement_request — validation
# ══════════════════════════════════════════════════════════════════════

class TestReplacementValidation:
    def test_valid_order_id(self):
        ok, args, err = validate_create_replacement_request_args({"order_id": 5})
        assert ok is True

    def test_negative_order_id_rejected(self):
        ok, args, err = validate_create_replacement_request_args({"order_id": -5})
        assert ok is False

    def test_boolean_order_id_rejected(self):
        ok, args, err = validate_create_replacement_request_args({"order_id": False})
        assert ok is False
        assert "boolean" in err.lower()

    def test_missing_order_id_rejected(self):
        ok, args, err = validate_create_replacement_request_args({})
        assert ok is False


# ══════════════════════════════════════════════════════════════════════
# create_replacement_request — business logic
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestReplacementExecution:
    def test_successful_replacement_for_shipped_order(self, db, customer, orders):
        result = create_replacement_request(
            order_id=1001,  # SHIPPED
            authenticated_customer_id=customer.id,
            db=db,
        )
        assert "error" not in result
        assert result["status"] == "PENDING"
        assert result["order_id"] == 1001

    def test_successful_replacement_for_delivered_order(self, db, customer, orders):
        result = create_replacement_request(
            order_id=1002,  # DELIVERED
            authenticated_customer_id=customer.id,
            db=db,
        )
        assert "error" not in result

    def test_successful_replacement_for_processing_order(self, db, customer, orders):
        result = create_replacement_request(
            order_id=1003,  # PROCESSING
            authenticated_customer_id=customer.id,
            db=db,
        )
        assert "error" not in result

    def test_unauthorized_replacement_blocked(self, db, customer, orders):
        # customer does not own order 1004
        result = create_replacement_request(
            order_id=1004,
            authenticated_customer_id=customer.id,
            db=db,
        )
        assert "error" in result
        assert "unauthorized" in result["error"].lower()

    def test_cancelled_order_not_eligible(self, db, customer, orders):
        # Order 1005 is CANCELLED
        result = create_replacement_request(
            order_id=1005,
            authenticated_customer_id=customer.id,
            db=db,
        )
        assert "error" in result
        assert "not eligible" in result["error"].lower()

    def test_nonexistent_order_returns_error(self, db, customer, orders):
        result = create_replacement_request(
            order_id=9999,
            authenticated_customer_id=customer.id,
            db=db,
        )
        assert "error" in result
        assert "not found" in result["error"].lower()

    def test_duplicate_replacement_prevented(self, db, customer, orders):
        create_replacement_request(
            order_id=1001,
            authenticated_customer_id=customer.id,
            db=db,
        )
        result = create_replacement_request(
            order_id=1001,
            authenticated_customer_id=customer.id,
            db=db,
        )
        assert "error" in result
        assert "already exists" in result["error"].lower()

    def test_replacement_writes_to_database(self, db, customer, orders):
        create_replacement_request(
            order_id=1002,
            authenticated_customer_id=customer.id,
            db=db,
        )
        record = db.query(ReplacementRequest).filter_by(order_id=1002).first()
        assert record is not None
        assert record.customer_id == customer.id
        assert record.status == "PENDING"


# ══════════════════════════════════════════════════════════════════════
# issue_refund — argument validation
# ══════════════════════════════════════════════════════════════════════

class TestRefundValidation:
    def test_valid_args(self):
        ok, args, err = validate_issue_refund_args({"order_id": 1, "amount": 50.0})
        assert ok is True

    def test_negative_amount_rejected(self):
        ok, args, err = validate_issue_refund_args({"order_id": 1, "amount": -10.0})
        assert ok is False

    def test_zero_amount_rejected(self):
        ok, args, err = validate_issue_refund_args({"order_id": 1, "amount": 0})
        assert ok is False

    def test_boolean_order_id_rejected(self):
        ok, args, err = validate_issue_refund_args({"order_id": True, "amount": 50.0})
        assert ok is False
        assert "boolean" in err.lower()

    def test_boolean_amount_rejected(self):
        ok, args, err = validate_issue_refund_args({"order_id": 1, "amount": True})
        assert ok is False
        assert "boolean" in err.lower()

    def test_missing_amount_rejected(self):
        ok, args, err = validate_issue_refund_args({"order_id": 1})
        assert ok is False

    def test_missing_order_id_rejected(self):
        ok, args, err = validate_issue_refund_args({"amount": 100.0})
        assert ok is False


# ══════════════════════════════════════════════════════════════════════
# issue_refund — business logic
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestRefundExecution:
    def test_low_value_refund_auto_approved(self, db, customer, orders):
        result = issue_refund(
            order_id=1006,
            amount=REFUND_AUTO_APPROVAL_THRESHOLD,  # exactly at threshold
            authenticated_customer_id=customer.id,
            db=db,
        )
        assert result["status"] == "COMPLETED"
        assert result["approval_required"] is False

    def test_high_value_refund_requires_approval(self, db, customer, orders):
        result = issue_refund(
            order_id=1006,
            amount=REFUND_AUTO_APPROVAL_THRESHOLD + 0.01,
            authenticated_customer_id=customer.id,
            db=db,
        )
        assert result["status"] == "PENDING_APPROVAL"
        assert result["approval_required"] is True
        assert "approval_id" in result

    def test_refund_creates_action_record(self, db, customer, orders):
        issue_refund(
            order_id=1006, amount=50.0,
            authenticated_customer_id=customer.id, db=db,
        )
        action = db.query(Action).filter_by(
            action_type="REFUND", reference_id=1006
        ).first()
        assert action is not None
        assert action.status == "COMPLETED"
        assert action.amount == 50.0

    def test_unauthorized_refund_blocked(self, db, customer, orders):
        result = issue_refund(
            order_id=1004,  # owned by customer2
            amount=100.0,
            authenticated_customer_id=customer.id,
            db=db,
        )
        assert "error" in result
        assert "unauthorized" in result["error"].lower()

    def test_nonexistent_order_refund_error(self, db, customer, orders):
        result = issue_refund(
            order_id=9999, amount=100.0,
            authenticated_customer_id=customer.id, db=db,
        )
        assert "error" in result
        assert "not found" in result["error"].lower()

    def test_duplicate_refund_prevented(self, db, customer, orders):
        issue_refund(
            order_id=1006, amount=50.0,
            authenticated_customer_id=customer.id, db=db,
        )
        result = issue_refund(
            order_id=1006, amount=50.0,
            authenticated_customer_id=customer.id, db=db,
        )
        assert "error" in result
        assert "already exists" in result["error"].lower()


# ══════════════════════════════════════════════════════════════════════
# Approval state transitions
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestApprovalTransitions:
    def _create_pending_refund(self, db, customer):
        """Helper: create a high-value refund pending approval."""
        return create_approval_request(
            action_type="REFUND",
            reference_id=1006,
            customer_id=customer.id,
            amount=5000.0,
            db=db,
        )

    def test_approve_transitions_action_to_completed(self, db, customer, orders):
        pending = self._create_pending_refund(db, customer)
        result = approve_request(
            approval_id=pending["approval_id"],
            resolved_by="admin",
            db=db,
        )
        assert result["approval_status"] == "APPROVED"
        assert result["action_status"] == "COMPLETED"
        assert result["executed"] is True

    def test_reject_transitions_action_to_rejected(self, db, customer, orders):
        pending = self._create_pending_refund(db, customer)
        result = reject_request(
            approval_id=pending["approval_id"],
            resolved_by="admin",
            reason="Exceeds policy limit",
            db=db,
        )
        assert result["approval_status"] == "REJECTED"
        assert result["action_status"] == "REJECTED"
        assert result["executed"] is False

    def test_cannot_approve_already_approved(self, db, customer, orders):
        pending = self._create_pending_refund(db, customer)
        approve_request(approval_id=pending["approval_id"], resolved_by="admin", db=db)
        result = approve_request(approval_id=pending["approval_id"], resolved_by="admin", db=db)
        assert "error" in result
        assert "already" in result["error"].lower()

    def test_cannot_reject_already_rejected(self, db, customer, orders):
        pending = self._create_pending_refund(db, customer)
        reject_request(approval_id=pending["approval_id"], resolved_by="admin", db=db)
        result = reject_request(approval_id=pending["approval_id"], resolved_by="admin", db=db)
        assert "error" in result

    def test_approve_nonexistent_returns_error(self, db):
        result = approve_request(approval_id=99999, resolved_by="admin", db=db)
        assert "error" in result

    def test_approval_writes_resolved_by_and_timestamp(self, db, customer, orders):
        pending = self._create_pending_refund(db, customer)
        approve_request(
            approval_id=pending["approval_id"],
            resolved_by="reviewer_jane",
            reason="Within policy",
            db=db,
        )
        approval = db.query(Approval).filter_by(id=pending["approval_id"]).first()
        assert approval.resolved_by == "reviewer_jane"
        assert approval.resolved_at is not None
        assert approval.reason == "Within policy"


# ══════════════════════════════════════════════════════════════════════
# _execute_tool_call dispatch layer
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestExecuteToolCallDispatch:
    def test_unknown_tool_rejected(self, db, customer, orders):
        result = _execute_tool_call(
            "delete_all_orders", {"order_id": 1001}, customer.id
        )
        assert result["validation_passed"] is False
        assert result["backend_executed"] is False
        assert "unknown tool" in result["tool_result"]["error"].lower()

    def test_get_order_status_authorized(self, db, customer, orders):
        result = _execute_tool_call(
            "get_order_status", {"order_id": 1001}, customer.id
        )
        assert result["validation_passed"] is True
        assert result["authorization_passed"] is True
        assert result["backend_executed"] is True
        assert result["tool_result"]["status"] == "SHIPPED"

    def test_get_order_status_unauthorized(self, db, customer, orders):
        result = _execute_tool_call(
            "get_order_status", {"order_id": 1004}, customer.id
        )
        assert result["authorization_passed"] is False
        assert result["backend_executed"] is False

    def test_get_order_status_invalid_args(self, db, customer, orders):
        result = _execute_tool_call(
            "get_order_status", {"order_id": -5}, customer.id
        )
        assert result["validation_passed"] is False
        assert result["backend_executed"] is False

    def test_issue_refund_produces_pending_approval_for_high_value(self, db, customer, orders):
        result = _execute_tool_call(
            "issue_refund", {"order_id": 1006, "amount": 9999.0}, customer.id
        )
        assert result["validation_passed"] is True
        assert result["tool_result"]["status"] == "PENDING_APPROVAL"
