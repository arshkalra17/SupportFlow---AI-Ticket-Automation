# pyrefly: ignore [missing-import]
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from app.database import SessionLocal, Base, engine
from app.models import Order, ReplacementRequest, Action, Approval
from app.approval import create_approval_request
from app.observability import traced_span, safe_set_attribute

# Ensure tables are created
Base.metadata.create_all(bind=engine)


class GetOrderStatusArgs(BaseModel):
    order_id: int = Field(gt=0, description="Unique positive integer ID of the order")


def validate_get_order_status_args(args: dict) -> tuple[bool, GetOrderStatusArgs | None, str | None]:
    """Validates raw arguments dictionary against GetOrderStatusArgs schema.

    Args:
        args (dict): Raw dictionary extracted from LLM tool call arguments.

    Returns:
        tuple[bool, GetOrderStatusArgs | None, str | None]:
            (is_valid, validated_model_instance, error_message_if_invalid)
    """
    if not isinstance(args, dict):
        return False, None, f"Tool arguments must be a JSON object/dict, got {type(args).__name__}"

    # Disallow boolean values passed as order_id (in Python bool is a subclass of int)
    if "order_id" in args and isinstance(args["order_id"], bool):
        return False, None, "Validation Error: order_id cannot be a boolean value"

    try:
        validated_args = GetOrderStatusArgs(**args)
        return True, validated_args, None
    except ValidationError as err:
        errors = err.errors()
        err_msg = errors[0].get("msg", str(err))
        field = errors[0].get("loc", ["order_id"])[0]
        return False, None, f"Validation Error: {err_msg} for field '{field}'"
    except Exception as err:
        return False, None, f"Validation Error: {err}"


def authorize_and_get_order_status(
    authenticated_customer_id: int,
    order_id: int,
    db: Session | None = None
) -> tuple[bool, str | None, dict | None]:
    """Checks order ownership in PostgreSQL and executes get_order_status if authorized.

    Args:
        authenticated_customer_id (int): ID of the authenticated user making the request.
        order_id (int): ID of the order being requested.
        db (Session, optional): SQLAlchemy DB session.

    Returns:
        tuple[bool, str | None, dict | None]:
            (authorized, auth_error_message, order_status_dict)
    """
    with traced_span("tool.authorize_and_get_order_status") as span:
        safe_set_attribute(span, "tool.name", "get_order_status")
        safe_set_attribute(span, "tool.order_id", order_id)

        close_db = False
        if db is None:
            db = SessionLocal()
            close_db = True

        try:
            order = db.query(Order).filter(Order.id == order_id).first()
            if not order:
                # Order not found -> safe not-found error
                safe_set_attribute(span, "tool.authorized", True)
                safe_set_attribute(span, "tool.order_found", False)
                return True, None, {"error": f"Order #{order_id} not found"}

            if order.customer_id != authenticated_customer_id:
                # Authorization failure -> do NOT expose order details
                safe_set_attribute(span, "tool.authorized", False)
                safe_set_attribute(span, "tool.order_found", True)
                return False, f"Unauthorized: Customer #{authenticated_customer_id} does not have permission to access Order #{order_id}", None

            safe_set_attribute(span, "tool.authorized", True)
            safe_set_attribute(span, "tool.order_found", True)
            safe_set_attribute(span, "tool.order_status", order.status)
            return True, None, {
                "order_id": order.id,
                "customer_id": order.customer_id,
                "status": order.status,
            }
        finally:
            if close_db:
                db.close()


def seed_sample_orders():
    """Seeds synthetic sample order data for testing if orders table is empty."""
    db = SessionLocal()
    try:
        if db.query(Order).count() == 0:
            sample_orders = [
                Order(id=1001, customer_id=1, status="SHIPPED"),
                Order(id=1002, customer_id=2, status="DELIVERED"),
                Order(id=1003, customer_id=1, status="PROCESSING"),
                Order(id=1004, customer_id=3, status="CANCELLED"),
            ]
            db.add_all(sample_orders)
            db.commit()
    finally:
        db.close()


def get_order_status(order_id: int, db: Session | None = None) -> dict:
    """Queries PostgreSQL database for an order by ID and returns its status.

    Args:
        order_id (int): Unique identifier of the order.
        db (Session, optional): SQLAlchemy DB session. If None, a new session is created.

    Returns:
        dict: Dictionary containing order_id and status on success, or an error message if not found.
    """
    if not isinstance(order_id, int) or order_id <= 0:
        return {"error": f"Invalid order_id: {order_id}"}

    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        order = db.query(Order).filter(Order.id == order_id).first()
        if not order:
            return {"error": f"Order #{order_id} not found"}

        return {
            "order_id": order.id,
            "customer_id": order.customer_id,
            "status": order.status,
        }
    finally:
        if close_db:
            db.close()


# ── Eligibility rule: orders eligible for replacement ────────────────
REPLACEMENT_ELIGIBLE_STATUSES = {"SHIPPED", "DELIVERED", "PROCESSING"}


class CreateReplacementRequestArgs(BaseModel):
    order_id: int = Field(gt=0, description="Unique positive integer ID of the order to replace")


def validate_create_replacement_request_args(args: dict) -> tuple[bool, CreateReplacementRequestArgs | None, str | None]:
    """Validates raw arguments for create_replacement_request.

    Args:
        args (dict): Raw arguments dictionary.

    Returns:
        tuple[bool, CreateReplacementRequestArgs | None, str | None]:
            (is_valid, validated_model_instance, error_message_if_invalid)
    """
    if not isinstance(args, dict):
        return False, None, f"Tool arguments must be a JSON object/dict, got {type(args).__name__}"

    if "order_id" in args and isinstance(args["order_id"], bool):
        return False, None, "Validation Error: order_id cannot be a boolean value"

    try:
        validated_args = CreateReplacementRequestArgs(**args)
        return True, validated_args, None
    except ValidationError as err:
        errors = err.errors()
        err_msg = errors[0].get("msg", str(err))
        field = errors[0].get("loc", ["order_id"])[0]
        return False, None, f"Validation Error: {err_msg} for field '{field}'"
    except Exception as err:
        return False, None, f"Validation Error: {err}"


def create_replacement_request(
    order_id: int,
    authenticated_customer_id: int,
    db: Session | None = None
) -> dict:
    """Creates a replacement request after validating ownership and eligibility.

    The backend enforces:
    1. Order must exist.
    2. Authenticated customer must own the order.
    3. Order status must be eligible for replacement (not CANCELLED).
    4. No duplicate active replacement request for the same order.

    Args:
        order_id (int): ID of the order to create a replacement for.
        authenticated_customer_id (int): ID of the authenticated customer.
        db (Session, optional): SQLAlchemy DB session.

    Returns:
        dict: Replacement request details on success, or a structured error.
    """
    with traced_span("tool.create_replacement_request") as span:
        safe_set_attribute(span, "tool.name", "create_replacement_request")
        safe_set_attribute(span, "tool.order_id", order_id)

        close_db = False
        if db is None:
            db = SessionLocal()
            close_db = True

        try:
            # 1. Fetch the order
            order = db.query(Order).filter(Order.id == order_id).first()
            if not order:
                safe_set_attribute(span, "tool.order_found", False)
                return {"error": f"Order #{order_id} not found"}

            safe_set_attribute(span, "tool.order_found", True)

            # 2. Authorization: verify ownership
            if order.customer_id != authenticated_customer_id:
                safe_set_attribute(span, "tool.authorized", False)
                return {"error": f"Unauthorized: Customer #{authenticated_customer_id} does not have permission to access Order #{order_id}"}

            safe_set_attribute(span, "tool.authorized", True)
            safe_set_attribute(span, "tool.order_status", order.status)

            # 3. Eligibility: deterministic backend rule
            if order.status not in REPLACEMENT_ELIGIBLE_STATUSES:
                safe_set_attribute(span, "tool.eligible", False)
                return {"error": f"Order #{order_id} is not eligible for replacement (current status: {order.status})"}

            safe_set_attribute(span, "tool.eligible", True)

            # 4. Duplicate prevention: check for existing active replacement request
            existing = (
                db.query(ReplacementRequest)
                .filter(
                    ReplacementRequest.order_id == order_id,
                    ReplacementRequest.status == "PENDING"
                )
                .first()
            )
            if existing:
                safe_set_attribute(span, "tool.duplicate_found", True)
                return {
                    "error": f"A replacement request already exists for Order #{order_id}",
                    "existing_replacement_id": existing.id,
                    "existing_status": existing.status,
                }

            safe_set_attribute(span, "tool.duplicate_found", False)

            # 5. Create the replacement request
            replacement = ReplacementRequest(
                order_id=order_id,
                customer_id=authenticated_customer_id,
                status="PENDING"
            )
            db.add(replacement)
            db.commit()
            db.refresh(replacement)

            safe_set_attribute(span, "tool.replacement_id", replacement.id)
            safe_set_attribute(span, "tool.status", "COMPLETED")
            return {
                "replacement_id": replacement.id,
                "order_id": replacement.order_id,
                "customer_id": replacement.customer_id,
                "status": replacement.status,
            }
        finally:
            if close_db:
                db.close()


# ── Risky Action: Issue Refund ────────────────────────────────────────

REFUND_AUTO_APPROVAL_THRESHOLD = 1000.0


class IssueRefundArgs(BaseModel):
    order_id: int = Field(gt=0, description="Unique positive integer ID of the order to refund")
    amount: float = Field(gt=0, description="Refund amount in USD, must be greater than 0")


def validate_issue_refund_args(args: dict) -> tuple[bool, IssueRefundArgs | None, str | None]:
    """Validates raw arguments for issue_refund.

    Args:
        args (dict): Raw arguments dictionary.

    Returns:
        tuple[bool, IssueRefundArgs | None, str | None]:
            (is_valid, validated_model_instance, error_message_if_invalid)
    """
    if not isinstance(args, dict):
        return False, None, f"Tool arguments must be a JSON object/dict, got {type(args).__name__}"

    if "order_id" in args and isinstance(args["order_id"], bool):
        return False, None, "Validation Error: order_id cannot be a boolean value"

    if "amount" in args and isinstance(args["amount"], bool):
        return False, None, "Validation Error: amount cannot be a boolean value"

    try:
        validated_args = IssueRefundArgs(**args)
        return True, validated_args, None
    except ValidationError as err:
        errors = err.errors()
        err_msg = errors[0].get("msg", str(err))
        field = errors[0].get("loc", ["amount"])[0]
        return False, None, f"Validation Error: {err_msg} for field '{field}'"
    except Exception as err:
        return False, None, f"Validation Error: {err}"


def issue_refund(
    order_id: int,
    amount: float,
    authenticated_customer_id: int,
    db: Session | None = None
) -> dict:
    """Issues a refund or creates a pending approval request based on backend risk policy.

    Order of checks:
    1. Validate parameters (handled via validate_issue_refund_args or inline check).
    2. Verify order exists.
    3. Verify ownership (authenticated_customer_id owns order).
    4. Duplicate check (no active or completed refund for order_id).
    5. Deterministic risk check:
       - amount <= 1000: execute synthetic refund automatically (Action COMPLETED).
       - amount > 1000: create Action & Approval records (PENDING_APPROVAL), return approval required.

    Args:
        order_id (int): Order ID to refund.
        amount (float): Monetary refund amount.
        authenticated_customer_id (int): Trusted customer identity.
        db (Session, optional): DB session.

    Returns:
        dict: Refund outcome or approval required details.
    """
    with traced_span("tool.issue_refund") as span:
        safe_set_attribute(span, "tool.name", "issue_refund")
        safe_set_attribute(span, "tool.order_id", order_id)
        safe_set_attribute(span, "tool.amount", amount)

        if not isinstance(order_id, int) or isinstance(order_id, bool) or order_id <= 0:
            safe_set_attribute(span, "tool.validation_error", True)
            return {"error": f"Validation Error: order_id must be a positive integer, got {order_id}"}

        if not isinstance(amount, (int, float)) or isinstance(amount, bool) or amount <= 0:
            safe_set_attribute(span, "tool.validation_error", True)
            return {"error": f"Validation Error: amount must be a positive number, got {amount}"}

        safe_set_attribute(span, "tool.validation_error", False)

        close_db = False
        if db is None:
            db = SessionLocal()
            close_db = True

        try:
            # 1. Verify order exists
            order = db.query(Order).filter(Order.id == order_id).first()
            if not order:
                safe_set_attribute(span, "tool.order_found", False)
                return {"error": f"Order #{order_id} not found"}

            safe_set_attribute(span, "tool.order_found", True)

            # 2. Authorization: verify ownership
            if order.customer_id != authenticated_customer_id:
                safe_set_attribute(span, "tool.authorized", False)
                return {
                    "error": (
                        f"Unauthorized: Customer #{authenticated_customer_id} does not "
                        f"have permission to access Order #{order_id}"
                    )
                }

            safe_set_attribute(span, "tool.authorized", True)

            # 3. Duplicate prevention check
            existing_action = (
                db.query(Action)
                .filter(
                    Action.action_type == "REFUND",
                    Action.reference_id == order_id,
                    Action.status.in_(["COMPLETED", "PENDING"])
                )
                .first()
            )
            if existing_action:
                safe_set_attribute(span, "tool.duplicate_found", True)
                safe_set_attribute(span, "tool.existing_action_id", existing_action.id)
                return {
                    "error": f"A refund request or completed refund already exists for Order #{order_id}",
                    "existing_action_id": existing_action.id,
                    "existing_status": existing_action.status,
                    "existing_amount": existing_action.amount,
                }

            safe_set_attribute(span, "tool.duplicate_found", False)

            # 4. Deterministic Risk Check
            approval_required = amount > REFUND_AUTO_APPROVAL_THRESHOLD
            safe_set_attribute(span, "tool.approval_required", approval_required)
            safe_set_attribute(span, "tool.threshold", REFUND_AUTO_APPROVAL_THRESHOLD)

            if not approval_required:
                # Low-value refund -> Execute automatically
                action = Action(
                    action_type="REFUND",
                    reference_id=order_id,
                    customer_id=authenticated_customer_id,
                    amount=amount,
                    status="COMPLETED",
                )
                db.add(action)
                db.commit()
                db.refresh(action)

                safe_set_attribute(span, "tool.action_id", action.id)
                safe_set_attribute(span, "tool.status", "COMPLETED")
                return {
                    "status": "COMPLETED",
                    "message": f"Refund of ${amount:.2f} issued successfully for Order #{order_id}.",
                    "order_id": order_id,
                    "customer_id": authenticated_customer_id,
                    "amount": amount,
                    "action_id": action.id,
                    "approval_required": False,
                }
            else:
                # High-value refund -> Trapped by risk rule, create PENDING Approval
                app_res = create_approval_request(
                    action_type="REFUND",
                    reference_id=order_id,
                    customer_id=authenticated_customer_id,
                    amount=amount,
                    db=db,
                )

                safe_set_attribute(span, "tool.action_id", app_res["action_id"])
                safe_set_attribute(span, "tool.approval_id", app_res["approval_id"])
                safe_set_attribute(span, "tool.status", "PENDING_APPROVAL")
                return {
                    "status": "PENDING_APPROVAL",
                    "message": (
                        f"Refund request of ${amount:.2f} for Order #{order_id} exceeds auto-approval "
                        f"threshold (${REFUND_AUTO_APPROVAL_THRESHOLD:.2f}). Human approval is required."
                    ),
                    "order_id": order_id,
                    "customer_id": authenticated_customer_id,
                    "amount": amount,
                    "action_id": app_res["action_id"],
                    "approval_id": app_res["approval_id"],
                    "approval_required": True,
                }
        finally:
            if close_db:
                db.close()

