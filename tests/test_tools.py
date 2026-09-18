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


# ══════════════════════════════════════════════════════════════════════
# NEW TOOLS TESTS: Stage 12+ Backend Tool Expansion
# ══════════════════════════════════════════════════════════════════════

# ── get_order_details tests ───────────────────────────────────────────

from app.tools import (
    validate_get_order_details_args,
    get_order_details,
    validate_list_customer_orders_args,
    list_customer_orders,
    validate_check_cancellation_eligibility_args,
    check_cancellation_eligibility,
    validate_get_delivery_estimate_args,
    get_delivery_estimate,
    validate_get_ticket_status_args,
    get_ticket_status,
    validate_get_customer_tickets_args,
    get_customer_tickets,
    CANCELLATION_ELIGIBLE_STATUSES,
)


class TestGetOrderDetailsValidation:
    def test_valid_order_id(self):
        ok, args, err = validate_get_order_details_args({"order_id": 1001})
        assert ok is True
        assert args.order_id == 1001
        assert err is None

    def test_negative_order_id_rejected(self):
        ok, args, err = validate_get_order_details_args({"order_id": -1})
        assert ok is False
        assert err is not None

    def test_boolean_order_id_rejected(self):
        ok, args, err = validate_get_order_details_args({"order_id": True})
        assert ok is False
        assert "boolean" in err.lower()

    def test_missing_order_id_rejected(self):
        ok, args, err = validate_get_order_details_args({})
        assert ok is False


@pytest.mark.integration
class TestGetOrderDetailsExecution:
    def test_owner_gets_order_details(self, db, customer, orders):
        result = get_order_details(
            order_id=1001,
            authenticated_customer_id=customer.id,
            db=db,
        )
        assert "error" not in result
        assert result["order_id"] == 1001
        assert result["status"] == "SHIPPED"
        assert result["total"] == 150.00
        assert result["items"] == [{"sku": "WIDGET-A", "quantity": 1, "price": 150.00}]
        assert result["created_at"] is not None

    def test_non_owner_blocked(self, db, customer, orders):
        result = get_order_details(
            order_id=1004,  # owned by customer2
            authenticated_customer_id=customer.id,
            db=db,
        )
        assert "error" in result
        assert "unauthorized" in result["error"].lower()

    def test_nonexistent_order_returns_error(self, db, customer, orders):
        result = get_order_details(
            order_id=9999,
            authenticated_customer_id=customer.id,
            db=db,
        )
        assert "error" in result
        assert "not found" in result["error"].lower()


# ── list_customer_orders tests ────────────────────────────────────────

class TestListCustomerOrdersValidation:
    def test_valid_args_with_filter(self):
        ok, args, err = validate_list_customer_orders_args({"status_filter": "SHIPPED", "limit": 5})
        assert ok is True
        assert args.status_filter == "SHIPPED"
        assert args.limit == 5

    def test_valid_args_no_filter(self):
        ok, args, err = validate_list_customer_orders_args({})
        assert ok is True
        assert args.status_filter is None
        assert args.limit == 10  # default

    def test_limit_too_high_rejected(self):
        ok, args, err = validate_list_customer_orders_args({"limit": 101})
        assert ok is False

    def test_limit_zero_rejected(self):
        ok, args, err = validate_list_customer_orders_args({"limit": 0})
        assert ok is False


@pytest.mark.integration
class TestListCustomerOrdersExecution:
    def test_lists_customer_orders(self, db, customer, orders):
        result = list_customer_orders(
            authenticated_customer_id=customer.id,
            db=db,
        )
        assert "orders" in result
        assert result["count"] == 5  # customer owns 5 orders (1001-1003, 1005-1006)
        assert len(result["orders"]) == 5

    def test_filter_by_status(self, db, customer, orders):
        result = list_customer_orders(
            authenticated_customer_id=customer.id,
            status_filter="DELIVERED",
            db=db,
        )
        assert result["count"] == 2  # orders 1002, 1006
        for order in result["orders"]:
            assert order["status"] == "DELIVERED"

    def test_respects_limit(self, db, customer, orders):
        result = list_customer_orders(
            authenticated_customer_id=customer.id,
            limit=2,
            db=db,
        )
        assert result["count"] == 2
        assert len(result["orders"]) == 2

    def test_customer_isolation(self, db, customer, customer2, orders):
        result = list_customer_orders(
            authenticated_customer_id=customer.id,
            db=db,
        )
        order_ids = [o["order_id"] for o in result["orders"]]
        assert 1004 not in order_ids  # order 1004 belongs to customer2

    def test_empty_result_for_customer_with_no_orders(self, db, customer2, orders):
        # customer2 only owns order 1004
        result = list_customer_orders(
            authenticated_customer_id=customer2.id,
            status_filter="CANCELLED",
            db=db,
        )
        assert result["count"] == 0
        assert result["orders"] == []


# ── check_cancellation_eligibility tests ──────────────────────────────

class TestCheckCancellationEligibilityValidation:
    def test_valid_order_id(self):
        ok, args, err = validate_check_cancellation_eligibility_args({"order_id": 1001})
        assert ok is True

    def test_boolean_order_id_rejected(self):
        ok, args, err = validate_check_cancellation_eligibility_args({"order_id": False})
        assert ok is False
        assert "boolean" in err.lower()


@pytest.mark.integration
class TestCheckCancellationEligibilityExecution:
    def test_processing_order_eligible(self, db, customer, orders):
        result = check_cancellation_eligibility(
            order_id=1003,  # PROCESSING
            authenticated_customer_id=customer.id,
            db=db,
        )
        assert "error" not in result
        assert result["eligible"] is True
        assert "PROCESSING" in result["reason"]

    def test_shipped_order_eligible(self, db, customer, orders):
        result = check_cancellation_eligibility(
            order_id=1001,  # SHIPPED
            authenticated_customer_id=customer.id,
            db=db,
        )
        assert result["eligible"] is True

    def test_delivered_order_not_eligible(self, db, customer, orders):
        result = check_cancellation_eligibility(
            order_id=1002,  # DELIVERED
            authenticated_customer_id=customer.id,
            db=db,
        )
        assert result["eligible"] is False
        assert "DELIVERED" in result["reason"]

    def test_cancelled_order_not_eligible(self, db, customer, orders):
        result = check_cancellation_eligibility(
            order_id=1005,  # CANCELLED
            authenticated_customer_id=customer.id,
            db=db,
        )
        assert result["eligible"] is False

    def test_unauthorized_access_blocked(self, db, customer, orders):
        result = check_cancellation_eligibility(
            order_id=1004,  # owned by customer2
            authenticated_customer_id=customer.id,
            db=db,
        )
        assert "error" in result
        assert "unauthorized" in result["error"].lower()

    def test_nonexistent_order_returns_error(self, db, customer, orders):
        result = check_cancellation_eligibility(
            order_id=9999,
            authenticated_customer_id=customer.id,
            db=db,
        )
        assert "error" in result
        assert "not found" in result["error"].lower()


# ── get_delivery_estimate tests ───────────────────────────────────────

class TestGetDeliveryEstimateValidation:
    def test_valid_order_id(self):
        ok, args, err = validate_get_delivery_estimate_args({"order_id": 1001})
        assert ok is True

    def test_boolean_order_id_rejected(self):
        ok, args, err = validate_get_delivery_estimate_args({"order_id": True})
        assert ok is False


@pytest.mark.integration
class TestGetDeliveryEstimateExecution:
    def test_delivered_order_returns_delivered_message(self, db, customer, orders):
        result = get_delivery_estimate(
            order_id=1002,  # DELIVERED
            authenticated_customer_id=customer.id,
            db=db,
        )
        assert "error" not in result
        assert result["status"] == "DELIVERED"
        assert "already been delivered" in result["message"]

    def test_shipped_order_returns_placeholder(self, db, customer, orders):
        result = get_delivery_estimate(
            order_id=1001,  # SHIPPED
            authenticated_customer_id=customer.id,
            db=db,
        )
        assert result["status"] == "SHIPPED"
        assert "not available" in result["message"]

    def test_processing_order_returns_placeholder(self, db, customer, orders):
        result = get_delivery_estimate(
            order_id=1003,  # PROCESSING
            authenticated_customer_id=customer.id,
            db=db,
        )
        assert result["status"] == "PROCESSING"
        assert "not available" in result["message"]

    def test_cancelled_order_returns_unavailable(self, db, customer, orders):
        result = get_delivery_estimate(
            order_id=1005,  # CANCELLED
            authenticated_customer_id=customer.id,
            db=db,
        )
        assert result["status"] == "CANCELLED"
        assert "not available" in result["message"]

    def test_unauthorized_access_blocked(self, db, customer, orders):
        result = get_delivery_estimate(
            order_id=1004,
            authenticated_customer_id=customer.id,
            db=db,
        )
        assert "error" in result
        assert "unauthorized" in result["error"].lower()


# ── get_ticket_status tests ───────────────────────────────────────────

class TestGetTicketStatusValidation:
    def test_valid_ticket_id(self):
        ok, args, err = validate_get_ticket_status_args({"ticket_id": 100})
        assert ok is True

    def test_boolean_ticket_id_rejected(self):
        ok, args, err = validate_get_ticket_status_args({"ticket_id": True})
        assert ok is False
        assert "boolean" in err.lower()

    def test_negative_ticket_id_rejected(self):
        ok, args, err = validate_get_ticket_status_args({"ticket_id": -1})
        assert ok is False


@pytest.mark.integration
class TestGetTicketStatusExecution:
    def test_returns_ticket_status(self, db, customer):
        from app.models import Ticket
        ticket = Ticket(
            customer_id=customer.id,  # must own the ticket
            customer_message="Help with order",
            status="PENDING",
            category="Order Issue",
            priority="HIGH",
        )
        db.add(ticket)
        db.commit()
        db.refresh(ticket)

        result = get_ticket_status(
            ticket_id=ticket.id,
            authenticated_customer_id=customer.id,
            db=db,
        )
        assert "error" not in result
        assert result["ticket_id"] == ticket.id
        assert result["status"] == "PENDING"
        assert result["category"] == "Order Issue"
        assert result["priority"] == "HIGH"

    def test_nonexistent_ticket_returns_error(self, db, customer):
        result = get_ticket_status(
            ticket_id=99999,
            authenticated_customer_id=customer.id,
            db=db,
        )
        assert "error" in result
        assert "not found" in result["error"].lower()


# ── get_customer_tickets tests ────────────────────────────────────────

class TestGetCustomerTicketsValidation:
    def test_valid_args(self):
        ok, args, err = validate_get_customer_tickets_args({"limit": 20})
        assert ok is True
        assert args.limit == 20

    def test_default_limit(self):
        ok, args, err = validate_get_customer_tickets_args({})
        assert ok is True
        assert args.limit == 10

    def test_limit_too_high_rejected(self):
        ok, args, err = validate_get_customer_tickets_args({"limit": 51})
        assert ok is False


@pytest.mark.integration
class TestGetCustomerTicketsExecution:
    def test_returns_tickets_list(self, db, customer):
        from app.models import Ticket
        tickets = [
            Ticket(customer_id=customer.id, customer_message="Issue 1", status="PENDING", category="Billing"),
            Ticket(customer_id=customer.id, customer_message="Issue 2", status="RESOLVED", category="Technical"),
            Ticket(customer_id=customer.id, customer_message="Issue 3", status="PENDING", category="Order Issue"),
        ]
        db.add_all(tickets)
        db.commit()

        result = get_customer_tickets(
            authenticated_customer_id=customer.id,
            db=db,
        )
        assert "tickets" in result
        assert result["count"] == 3
        assert len(result["tickets"]) == 3

    def test_respects_limit(self, db, customer):
        from app.models import Ticket
        tickets = [
            Ticket(customer_id=customer.id, customer_message=f"Issue {i}", status="PENDING", category="General")
            for i in range(15)
        ]
        db.add_all(tickets)
        db.commit()

        result = get_customer_tickets(
            authenticated_customer_id=customer.id,
            limit=5,
            db=db,
        )
        assert result["count"] == 5
        assert len(result["tickets"]) == 5

    def test_empty_result_when_no_tickets(self, db, customer):
        result = get_customer_tickets(
            authenticated_customer_id=customer.id,
            db=db,
        )
        assert result["count"] == 0
        assert result["tickets"] == []

    def test_customer_isolation(self, db, customer, customer2):
        """Tickets owned by customer2 must not appear for customer."""
        from app.models import Ticket
        # create 2 tickets owned by customer2
        t1 = Ticket(customer_id=customer2.id, customer_message="Other issue 1", status="PENDING")
        t2 = Ticket(customer_id=customer2.id, customer_message="Other issue 2", status="PENDING")
        # create 1 ticket owned by customer
        t3 = Ticket(customer_id=customer.id, customer_message="My issue", status="PENDING")
        db.add_all([t1, t2, t3])
        db.commit()

        result = get_customer_tickets(authenticated_customer_id=customer.id, db=db)
        assert result["count"] == 1
        assert result["tickets"][0]["ticket_id"] == t3.id


# ── _execute_tool_call dispatch tests for new tools ──────────────────

@pytest.mark.integration
class TestExecuteToolCallNewTools:
    def test_get_order_details_dispatch(self, db, customer, orders):
        result = _execute_tool_call(
            "get_order_details", {"order_id": 1001}, customer.id
        )
        assert result["validation_passed"] is True
        assert result["authorization_passed"] is True
        assert result["backend_executed"] is True
        assert result["tool_result"]["total"] == 150.00

    def test_list_customer_orders_dispatch(self, db, customer, orders):
        result = _execute_tool_call(
            "list_customer_orders", {"limit": 3}, customer.id
        )
        assert result["validation_passed"] is True
        assert result["backend_executed"] is True
        assert result["tool_result"]["count"] == 3

    def test_check_cancellation_eligibility_dispatch(self, db, customer, orders):
        result = _execute_tool_call(
            "check_cancellation_eligibility", {"order_id": 1003}, customer.id
        )
        assert result["validation_passed"] is True
        assert result["backend_executed"] is True
        assert result["tool_result"]["eligible"] is True

    def test_get_delivery_estimate_dispatch(self, db, customer, orders):
        result = _execute_tool_call(
            "get_delivery_estimate", {"order_id": 1002}, customer.id
        )
        assert result["validation_passed"] is True
        assert result["backend_executed"] is True
        assert "DELIVERED" in result["tool_result"]["status"]

    def test_get_ticket_status_dispatch(self, db, customer):
        from app.models import Ticket
        ticket = Ticket(customer_id=customer.id, customer_message="Test", status="PENDING")
        db.add(ticket)
        db.commit()
        db.refresh(ticket)

        result = _execute_tool_call(
            "get_ticket_status", {"ticket_id": ticket.id}, customer.id
        )
        assert result["validation_passed"] is True
        assert result["backend_executed"] is True
        assert result["tool_result"]["status"] == "PENDING"

    def test_get_customer_tickets_dispatch(self, db, customer):
        result = _execute_tool_call(
            "get_customer_tickets", {}, customer.id
        )
        assert result["validation_passed"] is True
        assert result["backend_executed"] is True
        assert "tickets" in result["tool_result"]


# ── Contextual follow-up tests ────────────────────────────────────────

@pytest.mark.integration
class TestContextualToolUsage:
    """Tests that demonstrate how new tools work with active_order_id context."""

    def test_list_orders_then_get_details(self, db, customer, orders):
        # Step 1: List orders
        list_result = _execute_tool_call(
            "list_customer_orders", {"status_filter": "SHIPPED"}, customer.id
        )
        assert list_result["tool_result"]["count"] >= 1
        
        # Step 2: Get details of first order
        first_order_id = list_result["tool_result"]["orders"][0]["order_id"]
        details_result = _execute_tool_call(
            "get_order_details", {"order_id": first_order_id}, customer.id
        )
        assert details_result["backend_executed"] is True
        assert details_result["tool_result"]["order_id"] == first_order_id

    def test_check_eligibility_before_action(self, db, customer, orders):
        # Check if order can be cancelled
        check_result = _execute_tool_call(
            "check_cancellation_eligibility", {"order_id": 1003}, customer.id
        )
        assert check_result["tool_result"]["eligible"] is True
        
        # This demonstrates the workflow: check eligibility first,
        # then perform cancellation (not implemented yet)

    def test_get_details_then_check_delivery(self, db, customer, orders):
        # Step 1: Get order details
        details = _execute_tool_call(
            "get_order_details", {"order_id": 1001}, customer.id
        )
        assert details["tool_result"]["status"] == "SHIPPED"
        
        # Step 2: Check delivery estimate
        delivery = _execute_tool_call(
            "get_delivery_estimate", {"order_id": 1001}, customer.id
        )
        assert "SHIPPED" in delivery["tool_result"]["status"]


# ══════════════════════════════════════════════════════════════════════
# HARDENING: cancel_order tests
# ══════════════════════════════════════════════════════════════════════

from app.tools import validate_cancel_order_args, cancel_order
from app.models import Action as ActionModel


class TestCancelOrderValidation:
    def test_valid_order_id(self):
        ok, args, err = validate_cancel_order_args({"order_id": 1001})
        assert ok is True
        assert args.order_id == 1001

    def test_negative_order_id_rejected(self):
        ok, args, err = validate_cancel_order_args({"order_id": -1})
        assert ok is False

    def test_boolean_order_id_rejected(self):
        ok, args, err = validate_cancel_order_args({"order_id": True})
        assert ok is False
        assert "boolean" in err.lower()

    def test_missing_order_id_rejected(self):
        ok, args, err = validate_cancel_order_args({})
        assert ok is False


@pytest.mark.integration
class TestCancelOrderExecution:
    def test_cancels_processing_order(self, db, customer, orders):
        """order 1003 is PROCESSING — eligible for cancellation."""
        result = cancel_order(order_id=1003, authenticated_customer_id=customer.id, db=db)
        assert "error" not in result
        assert result["status"] == "COMPLETED"
        assert result["order_id"] == 1003
        assert result["previous_status"] == "PROCESSING"
        assert result["order_status"] == "CANCELLED"
        assert "action_id" in result

    def test_cancels_shipped_order(self, db, customer, orders):
        """order 1001 is SHIPPED — eligible for cancellation."""
        result = cancel_order(order_id=1001, authenticated_customer_id=customer.id, db=db)
        assert result["status"] == "COMPLETED"
        assert result["order_status"] == "CANCELLED"

    def test_writes_cancellation_to_database(self, db, customer, orders):
        cancel_order(order_id=1003, authenticated_customer_id=customer.id, db=db)

        # Verify order status in DB
        from app.models import Order as OrderModel
        order = db.query(OrderModel).filter_by(id=1003).first()
        assert order.status == "CANCELLED"

        # Verify action record created
        action = db.query(ActionModel).filter_by(
            action_type="CANCELLATION", reference_id=1003
        ).first()
        assert action is not None
        assert action.status == "COMPLETED"
        assert action.customer_id == customer.id

    def test_delivered_order_not_eligible(self, db, customer, orders):
        """order 1002 is DELIVERED — cannot be cancelled."""
        result = cancel_order(order_id=1002, authenticated_customer_id=customer.id, db=db)
        assert "error" in result
        assert "cannot be cancelled" in result["error"].lower()

    def test_already_cancelled_is_idempotent(self, db, customer, orders):
        """order 1005 is already CANCELLED — returns ALREADY_CANCELLED."""
        result = cancel_order(order_id=1005, authenticated_customer_id=customer.id, db=db)
        assert result["status"] == "ALREADY_CANCELLED"
        assert "error" not in result

    def test_unauthorized_cancellation_blocked(self, db, customer, orders):
        """customer cannot cancel order 1004 owned by customer2."""
        result = cancel_order(order_id=1004, authenticated_customer_id=customer.id, db=db)
        assert "error" in result
        assert "unauthorized" in result["error"].lower()

    def test_nonexistent_order_returns_error(self, db, customer, orders):
        result = cancel_order(order_id=9999, authenticated_customer_id=customer.id, db=db)
        assert "error" in result
        assert "not found" in result["error"].lower()

    def test_duplicate_cancellation_action_prevented(self, db, customer, orders):
        """Second cancel_order call on same already-cancelled order stays idempotent."""
        cancel_order(order_id=1003, authenticated_customer_id=customer.id, db=db)
        result = cancel_order(order_id=1003, authenticated_customer_id=customer.id, db=db)
        # Order is now CANCELLED, second call should return ALREADY_CANCELLED
        assert result["status"] == "ALREADY_CANCELLED"


@pytest.mark.integration
class TestCancelOrderDispatch:
    def test_cancel_order_dispatch_succeeds(self, db, customer, orders):
        result = _execute_tool_call(
            "cancel_order", {"order_id": 1003}, customer.id
        )
        assert result["validation_passed"] is True
        assert result["authorization_passed"] is True
        assert result["backend_executed"] is True
        assert result["tool_result"]["status"] == "COMPLETED"

    def test_cancel_order_dispatch_unauthorized(self, db, customer, orders):
        result = _execute_tool_call(
            "cancel_order", {"order_id": 1004}, customer.id
        )
        assert result["authorization_passed"] is False
        assert result["backend_executed"] is False

    def test_cancel_order_dispatch_ineligible(self, db, customer, orders):
        result = _execute_tool_call(
            "cancel_order", {"order_id": 1002}, customer.id  # DELIVERED
        )
        assert result["authorization_passed"] is True
        assert result["backend_executed"] is False
        assert "error" in result["tool_result"]

    def test_cancel_order_dispatch_invalid_args(self, db, customer, orders):
        result = _execute_tool_call(
            "cancel_order", {"order_id": -1}, customer.id
        )
        assert result["validation_passed"] is False
        assert result["backend_executed"] is False


# ══════════════════════════════════════════════════════════════════════
# HARDENING: Ticket ownership tests
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestTicketOwnershipEnforcement:
    def test_get_ticket_status_own_ticket(self, db, customer, tickets):
        own_ticket = tickets[0]  # owned by customer
        result = get_ticket_status(
            ticket_id=own_ticket.id,
            authenticated_customer_id=customer.id,
            db=db,
        )
        assert "error" not in result
        assert result["ticket_id"] == own_ticket.id

    def test_get_ticket_status_other_customer_blocked(self, db, customer, customer2, tickets):
        other_ticket = tickets[2]  # owned by customer2
        result = get_ticket_status(
            ticket_id=other_ticket.id,
            authenticated_customer_id=customer.id,
            db=db,
        )
        assert "error" in result
        assert "unauthorized" in result["error"].lower()

    def test_get_customer_tickets_isolation(self, db, customer, customer2, tickets):
        """Each customer sees only their own tickets."""
        result_c1 = get_customer_tickets(authenticated_customer_id=customer.id, db=db)
        result_c2 = get_customer_tickets(authenticated_customer_id=customer2.id, db=db)

        ids_c1 = {t["ticket_id"] for t in result_c1["tickets"]}
        ids_c2 = {t["ticket_id"] for t in result_c2["tickets"]}

        assert ids_c1.isdisjoint(ids_c2), "Ticket sets must not overlap"
        assert result_c1["count"] == 2   # customer owns 2 tickets
        assert result_c2["count"] == 1   # customer2 owns 1 ticket

    def test_get_ticket_status_dispatch_unauthorized(self, db, customer, customer2, tickets):
        other_ticket = tickets[2]  # owned by customer2
        result = _execute_tool_call(
            "get_ticket_status", {"ticket_id": other_ticket.id}, customer.id
        )
        assert result["authorization_passed"] is False
        assert result["backend_executed"] is False


# ══════════════════════════════════════════════════════════════════════
# HARDENING: status_filter allowlist tests
# ══════════════════════════════════════════════════════════════════════

class TestListCustomerOrdersStatusFilter:
    def test_valid_statuses_accepted(self):
        for status in ["SHIPPED", "DELIVERED", "PROCESSING", "CANCELLED",
                       "shipped", "delivered", "processing", "cancelled"]:
            ok, args, err = validate_list_customer_orders_args({"status_filter": status})
            assert ok is True, f"Expected {status!r} to be valid"

    def test_invalid_status_rejected(self):
        ok, args, err = validate_list_customer_orders_args({"status_filter": "HACKED"})
        assert ok is False
        assert "status_filter" in err

    def test_unknown_status_rejected(self):
        ok, args, err = validate_list_customer_orders_args({"status_filter": "PENDING_DELETION"})
        assert ok is False

    def test_none_filter_accepted(self):
        ok, args, err = validate_list_customer_orders_args({})
        assert ok is True
        assert args.status_filter is None

    def test_status_normalised_to_uppercase(self):
        ok, args, err = validate_list_customer_orders_args({"status_filter": "shipped"})
        assert ok is True
        assert args.status_filter == "SHIPPED"


# ══════════════════════════════════════════════════════════════════════
# HARDENING: Response grounding regression tests
# These tests verify that certain tool results do NOT lead to incorrect
# claims — they test the tool_result structure that the system prompt
# instructs the LLM to relay correctly.
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestResponseGrounding:
    """Verifies tool_result structures that the LLM must report accurately."""

    def test_check_eligibility_result_is_read_only_eligible(self, db, customer, orders):
        """check_cancellation_eligibility must return eligible=True without modifying DB."""
        from app.models import Order as OrderModel
        result = check_cancellation_eligibility(
            order_id=1003, authenticated_customer_id=customer.id, db=db
        )
        assert result["eligible"] is True
        assert "error" not in result
        # Order must NOT have been modified
        order = db.query(OrderModel).filter_by(id=1003).first()
        assert order.status == "PROCESSING"  # unchanged

    def test_check_eligibility_result_is_read_only_ineligible(self, db, customer, orders):
        """Ineligible check must not modify order."""
        from app.models import Order as OrderModel
        result = check_cancellation_eligibility(
            order_id=1002, authenticated_customer_id=customer.id, db=db  # DELIVERED
        )
        assert result["eligible"] is False
        order = db.query(OrderModel).filter_by(id=1002).first()
        assert order.status == "DELIVERED"  # unchanged

    def test_refund_pending_approval_result_shows_not_completed(self, db, customer, orders):
        """High-value refund must return PENDING_APPROVAL, not COMPLETED."""
        from app.tools import issue_refund, REFUND_AUTO_APPROVAL_THRESHOLD
        result = issue_refund(
            order_id=1006,
            amount=REFUND_AUTO_APPROVAL_THRESHOLD + 0.01,
            authenticated_customer_id=customer.id,
            db=db,
        )
        assert result["status"] == "PENDING_APPROVAL"
        assert result["approval_required"] is True
        # The tool_result must NOT contain "COMPLETED"
        assert result.get("status") != "COMPLETED"

    def test_refund_completed_result_shows_completed(self, db, customer, orders):
        """Low-value refund must return COMPLETED."""
        from app.tools import issue_refund
        result = issue_refund(
            order_id=1006,
            amount=50.0,
            authenticated_customer_id=customer.id,
            db=db,
        )
        assert result["status"] == "COMPLETED"
        assert result["approval_required"] is False

    def test_cancel_order_completed_result_shows_cancelled(self, db, customer, orders):
        """cancel_order must return COMPLETED and DB must reflect cancellation."""
        from app.models import Order as OrderModel
        result = cancel_order(order_id=1003, authenticated_customer_id=customer.id, db=db)
        assert result["status"] == "COMPLETED"
        order = db.query(OrderModel).filter_by(id=1003).first()
        assert order.status == "CANCELLED"

    def test_get_delivery_estimate_unavailable_not_invented(self, db, customer, orders):
        """Delivery estimate must say 'not available', never invent a date."""
        result = get_delivery_estimate(
            order_id=1001,  # SHIPPED — no real ETA
            authenticated_customer_id=customer.id,
            db=db,
        )
        assert "error" not in result
        assert "not available" in result["message"].lower()
        # Must not contain any date-looking string — just check no fabricated key
        assert "estimated_at" not in result
        assert "delivery_date" not in result

    def test_list_orders_returns_only_own_orders(self, db, customer, customer2, orders):
        """list_customer_orders must not return other customers' orders."""
        result = list_customer_orders(authenticated_customer_id=customer.id, db=db)
        for order in result["orders"]:
            # All returned orders must be accessible by this customer
            # (We can't check customer_id directly from the result, but we can
            # verify get_order_details succeeds for each one)
            detail = get_order_details(
                order_id=order["order_id"],
                authenticated_customer_id=customer.id,
                db=db,
            )
            assert "error" not in detail, f"Order {order['order_id']} leaked to wrong customer"
