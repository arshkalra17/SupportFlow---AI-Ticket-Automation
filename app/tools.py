# pyrefly: ignore [missing-import]
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from app.database import SessionLocal, Base, engine
from app.models import Order, ReplacementRequest, Action, Approval, Ticket
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



# ══════════════════════════════════════════════════════════════════════
# NEW TOOLS: Stage 12+ Backend Tool Expansion
# ══════════════════════════════════════════════════════════════════════

# ── Tool 1: get_order_details ────────────────────────────────────────

class GetOrderDetailsArgs(BaseModel):
    order_id: int = Field(gt=0, description="Unique positive integer ID of the order")


def validate_get_order_details_args(args: dict) -> tuple[bool, GetOrderDetailsArgs | None, str | None]:
    """Validates raw arguments dictionary against GetOrderDetailsArgs schema.

    Args:
        args (dict): Raw dictionary extracted from LLM tool call arguments.

    Returns:
        tuple[bool, GetOrderDetailsArgs | None, str | None]:
            (is_valid, validated_model_instance, error_message_if_invalid)
    """
    if not isinstance(args, dict):
        return False, None, f"Tool arguments must be a JSON object/dict, got {type(args).__name__}"

    if "order_id" in args and isinstance(args["order_id"], bool):
        return False, None, "Validation Error: order_id cannot be a boolean value"

    try:
        validated_args = GetOrderDetailsArgs(**args)
        return True, validated_args, None
    except ValidationError as err:
        errors = err.errors()
        err_msg = errors[0].get("msg", str(err))
        field = errors[0].get("loc", ["order_id"])[0]
        return False, None, f"Validation Error: {err_msg} for field '{field}'"
    except Exception as err:
        return False, None, f"Validation Error: {err}"


def get_order_details(
    order_id: int,
    authenticated_customer_id: int,
    db: Session | None = None
) -> dict:
    """Retrieves detailed order information after verifying ownership.

    Args:
        order_id (int): ID of the order to retrieve.
        authenticated_customer_id (int): ID of the authenticated customer.
        db (Session, optional): SQLAlchemy DB session.

    Returns:
        dict: Order details on success, or a structured error.
    """
    with traced_span("tool.get_order_details") as span:
        safe_set_attribute(span, "tool.name", "get_order_details")
        safe_set_attribute(span, "tool.order_id", order_id)

        close_db = False
        if db is None:
            db = SessionLocal()
            close_db = True

        try:
            order = db.query(Order).filter(Order.id == order_id).first()
            if not order:
                safe_set_attribute(span, "tool.order_found", False)
                return {"error": f"Order #{order_id} not found"}

            safe_set_attribute(span, "tool.order_found", True)

            if order.customer_id != authenticated_customer_id:
                safe_set_attribute(span, "tool.authorized", False)
                return {
                    "error": (
                        f"Unauthorized: Customer #{authenticated_customer_id} does not "
                        f"have permission to access Order #{order_id}"
                    )
                }

            safe_set_attribute(span, "tool.authorized", True)
            safe_set_attribute(span, "tool.order_status", order.status)

            return {
                "order_id": order.id,
                "customer_id": order.customer_id,
                "status": order.status,
                "total": order.total,
                "items": order.items,
                "created_at": order.created_at.isoformat() if order.created_at else None,
            }
        finally:
            if close_db:
                db.close()


# ── Tool 2: list_customer_orders ─────────────────────────────────────

# Valid order status values (single source of truth)
VALID_ORDER_STATUSES = {"SHIPPED", "DELIVERED", "PROCESSING", "CANCELLED"}


class ListCustomerOrdersArgs(BaseModel):
    status_filter: str | None = Field(
        None,
        description="Optional status filter: SHIPPED, DELIVERED, PROCESSING, or CANCELLED"
    )
    limit: int = Field(10, ge=1, le=100, description="Maximum number of orders to return (1-100)")


def validate_list_customer_orders_args(args: dict) -> tuple[bool, ListCustomerOrdersArgs | None, str | None]:
    """Validates raw arguments for list_customer_orders.

    Args:
        args (dict): Raw arguments dictionary.

    Returns:
        tuple[bool, ListCustomerOrdersArgs | None, str | None]:
            (is_valid, validated_model_instance, error_message_if_invalid)
    """
    if not isinstance(args, dict):
        return False, None, f"Tool arguments must be a JSON object/dict, got {type(args).__name__}"

    # Validate status_filter against known values before Pydantic
    if "status_filter" in args and args["status_filter"] is not None:
        sf = str(args["status_filter"]).upper()
        if sf not in VALID_ORDER_STATUSES:
            return False, None, (
                f"Validation Error: status_filter must be one of "
                f"{sorted(VALID_ORDER_STATUSES)}, got '{args['status_filter']}'"
            )
        # Normalize to uppercase so downstream query is clean
        args = {**args, "status_filter": sf}

    try:
        validated_args = ListCustomerOrdersArgs(**args)
        return True, validated_args, None
    except ValidationError as err:
        errors = err.errors()
        err_msg = errors[0].get("msg", str(err))
        field = errors[0].get("loc", ["unknown"])[0]
        return False, None, f"Validation Error: {err_msg} for field '{field}'"
    except Exception as err:
        return False, None, f"Validation Error: {err}"


def list_customer_orders(
    authenticated_customer_id: int,
    status_filter: str | None = None,
    limit: int = 10,
    db: Session | None = None
) -> dict:
    """Lists orders for the authenticated customer with optional status filter.

    Args:
        authenticated_customer_id (int): ID of the authenticated customer.
        status_filter (str | None): Optional status to filter by.
        limit (int): Maximum number of orders to return.
        db (Session, optional): SQLAlchemy DB session.

    Returns:
        dict: List of orders or empty list.
    """
    with traced_span("tool.list_customer_orders") as span:
        safe_set_attribute(span, "tool.name", "list_customer_orders")
        safe_set_attribute(span, "tool.customer_id", authenticated_customer_id)
        if status_filter:
            safe_set_attribute(span, "tool.status_filter", status_filter)
        safe_set_attribute(span, "tool.limit", limit)

        close_db = False
        if db is None:
            db = SessionLocal()
            close_db = True

        try:
            query = db.query(Order).filter(Order.customer_id == authenticated_customer_id)
            
            if status_filter:
                query = query.filter(Order.status == status_filter.upper())
            
            orders = query.order_by(Order.created_at.desc()).limit(limit).all()

            safe_set_attribute(span, "tool.orders_found", len(orders))

            return {
                "orders": [
                    {
                        "order_id": order.id,
                        "status": order.status,
                        "total": order.total,
                        "created_at": order.created_at.isoformat() if order.created_at else None,
                    }
                    for order in orders
                ],
                "count": len(orders),
            }
        finally:
            if close_db:
                db.close()


# ── Tool 3: check_cancellation_eligibility ───────────────────────────

CANCELLATION_ELIGIBLE_STATUSES = {"PROCESSING", "SHIPPED"}


class CheckCancellationEligibilityArgs(BaseModel):
    order_id: int = Field(gt=0, description="Unique positive integer ID of the order")


def validate_check_cancellation_eligibility_args(args: dict) -> tuple[bool, CheckCancellationEligibilityArgs | None, str | None]:
    """Validates raw arguments for check_cancellation_eligibility.

    Args:
        args (dict): Raw arguments dictionary.

    Returns:
        tuple[bool, CheckCancellationEligibilityArgs | None, str | None]:
            (is_valid, validated_model_instance, error_message_if_invalid)
    """
    if not isinstance(args, dict):
        return False, None, f"Tool arguments must be a JSON object/dict, got {type(args).__name__}"

    if "order_id" in args and isinstance(args["order_id"], bool):
        return False, None, "Validation Error: order_id cannot be a boolean value"

    try:
        validated_args = CheckCancellationEligibilityArgs(**args)
        return True, validated_args, None
    except ValidationError as err:
        errors = err.errors()
        err_msg = errors[0].get("msg", str(err))
        field = errors[0].get("loc", ["order_id"])[0]
        return False, None, f"Validation Error: {err_msg} for field '{field}'"
    except Exception as err:
        return False, None, f"Validation Error: {err}"


def check_cancellation_eligibility(
    order_id: int,
    authenticated_customer_id: int,
    db: Session | None = None
) -> dict:
    """Checks if an order can be cancelled without actually cancelling it.

    Args:
        order_id (int): ID of the order to check.
        authenticated_customer_id (int): ID of the authenticated customer.
        db (Session, optional): SQLAlchemy DB session.

    Returns:
        dict: Eligibility status with explanation.
    """
    with traced_span("tool.check_cancellation_eligibility") as span:
        safe_set_attribute(span, "tool.name", "check_cancellation_eligibility")
        safe_set_attribute(span, "tool.order_id", order_id)

        close_db = False
        if db is None:
            db = SessionLocal()
            close_db = True

        try:
            order = db.query(Order).filter(Order.id == order_id).first()
            if not order:
                safe_set_attribute(span, "tool.order_found", False)
                return {"error": f"Order #{order_id} not found"}

            safe_set_attribute(span, "tool.order_found", True)

            if order.customer_id != authenticated_customer_id:
                safe_set_attribute(span, "tool.authorized", False)
                return {
                    "error": (
                        f"Unauthorized: Customer #{authenticated_customer_id} does not "
                        f"have permission to access Order #{order_id}"
                    )
                }

            safe_set_attribute(span, "tool.authorized", True)
            safe_set_attribute(span, "tool.order_status", order.status)

            eligible = order.status in CANCELLATION_ELIGIBLE_STATUSES
            safe_set_attribute(span, "tool.eligible", eligible)

            if eligible:
                return {
                    "order_id": order.id,
                    "eligible": True,
                    "reason": f"Order is in {order.status} status and can be cancelled.",
                }
            else:
                return {
                    "order_id": order.id,
                    "eligible": False,
                    "reason": f"Order is in {order.status} status and cannot be cancelled.",
                }
        finally:
            if close_db:
                db.close()


# ── Tool 4: get_delivery_estimate ────────────────────────────────────

class GetDeliveryEstimateArgs(BaseModel):
    order_id: int = Field(gt=0, description="Unique positive integer ID of the order")


def validate_get_delivery_estimate_args(args: dict) -> tuple[bool, GetDeliveryEstimateArgs | None, str | None]:
    """Validates raw arguments for get_delivery_estimate.

    Args:
        args (dict): Raw arguments dictionary.

    Returns:
        tuple[bool, GetDeliveryEstimateArgs | None, str | None]:
            (is_valid, validated_model_instance, error_message_if_invalid)
    """
    if not isinstance(args, dict):
        return False, None, f"Tool arguments must be a JSON object/dict, got {type(args).__name__}"

    if "order_id" in args and isinstance(args["order_id"], bool):
        return False, None, "Validation Error: order_id cannot be a boolean value"

    try:
        validated_args = GetDeliveryEstimateArgs(**args)
        return True, validated_args, None
    except ValidationError as err:
        errors = err.errors()
        err_msg = errors[0].get("msg", str(err))
        field = errors[0].get("loc", ["order_id"])[0]
        return False, None, f"Validation Error: {err_msg} for field '{field}'"
    except Exception as err:
        return False, None, f"Validation Error: {err}"


def get_delivery_estimate(
    order_id: int,
    authenticated_customer_id: int,
    db: Session | None = None
) -> dict:
    """Returns delivery estimate information if available (fail-safe, no error if data unavailable).

    Args:
        order_id (int): ID of the order.
        authenticated_customer_id (int): ID of the authenticated customer.
        db (Session, optional): SQLAlchemy DB session.

    Returns:
        dict: Delivery information or placeholder message.
    """
    with traced_span("tool.get_delivery_estimate") as span:
        safe_set_attribute(span, "tool.name", "get_delivery_estimate")
        safe_set_attribute(span, "tool.order_id", order_id)

        close_db = False
        if db is None:
            db = SessionLocal()
            close_db = True

        try:
            order = db.query(Order).filter(Order.id == order_id).first()
            if not order:
                safe_set_attribute(span, "tool.order_found", False)
                return {"error": f"Order #{order_id} not found"}

            safe_set_attribute(span, "tool.order_found", True)

            if order.customer_id != authenticated_customer_id:
                safe_set_attribute(span, "tool.authorized", False)
                return {
                    "error": (
                        f"Unauthorized: Customer #{authenticated_customer_id} does not "
                        f"have permission to access Order #{order_id}"
                    )
                }

            safe_set_attribute(span, "tool.authorized", True)
            safe_set_attribute(span, "tool.order_status", order.status)

            # Fail-safe: return placeholder if no delivery data
            # In production this would integrate with shipping provider API
            if order.status == "DELIVERED":
                return {
                    "order_id": order.id,
                    "status": "DELIVERED",
                    "message": "Order has already been delivered.",
                }
            elif order.status in ("SHIPPED", "PROCESSING"):
                return {
                    "order_id": order.id,
                    "status": order.status,
                    "message": f"Order is {order.status}. Delivery estimate information is not available at this time.",
                }
            else:
                return {
                    "order_id": order.id,
                    "status": order.status,
                    "message": "Delivery estimate not available for this order status.",
                }
        finally:
            if close_db:
                db.close()


# ── Tool 5: get_ticket_status ────────────────────────────────────────

class GetTicketStatusArgs(BaseModel):
    ticket_id: int = Field(gt=0, description="Unique positive integer ID of the ticket")


def validate_get_ticket_status_args(args: dict) -> tuple[bool, GetTicketStatusArgs | None, str | None]:
    """Validates raw arguments for get_ticket_status.

    Args:
        args (dict): Raw arguments dictionary.

    Returns:
        tuple[bool, GetTicketStatusArgs | None, str | None]:
            (is_valid, validated_model_instance, error_message_if_invalid)
    """
    if not isinstance(args, dict):
        return False, None, f"Tool arguments must be a JSON object/dict, got {type(args).__name__}"

    if "ticket_id" in args and isinstance(args["ticket_id"], bool):
        return False, None, "Validation Error: ticket_id cannot be a boolean value"

    try:
        validated_args = GetTicketStatusArgs(**args)
        return True, validated_args, None
    except ValidationError as err:
        errors = err.errors()
        err_msg = errors[0].get("msg", str(err))
        field = errors[0].get("loc", ["ticket_id"])[0]
        return False, None, f"Validation Error: {err_msg} for field '{field}'"
    except Exception as err:
        return False, None, f"Validation Error: {err}"


def get_ticket_status(
    ticket_id: int,
    authenticated_customer_id: int,
    db: Session | None = None
) -> dict:
    """Retrieves ticket status after verifying customer ownership.

    Ticket.customer_id must match the authenticated customer. Tickets
    without a customer_id (legacy) are not accessible via this tool.

    Args:
        ticket_id (int): ID of the ticket.
        authenticated_customer_id (int): ID of the authenticated customer.
        db (Session, optional): SQLAlchemy DB session.

    Returns:
        dict: Ticket status or error.
    """
    with traced_span("tool.get_ticket_status") as span:
        safe_set_attribute(span, "tool.name", "get_ticket_status")
        safe_set_attribute(span, "tool.ticket_id", ticket_id)

        close_db = False
        if db is None:
            db = SessionLocal()
            close_db = True

        try:
            ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
            if not ticket:
                safe_set_attribute(span, "tool.ticket_found", False)
                return {"error": f"Ticket #{ticket_id} not found"}

            safe_set_attribute(span, "tool.ticket_found", True)

            # Authorization: verify customer owns this ticket
            if ticket.customer_id != authenticated_customer_id:
                safe_set_attribute(span, "tool.authorized", False)
                return {
                    "error": (
                        f"Unauthorized: Customer #{authenticated_customer_id} does not "
                        f"have permission to access Ticket #{ticket_id}"
                    )
                }

            safe_set_attribute(span, "tool.authorized", True)
            safe_set_attribute(span, "tool.ticket_status", ticket.status)

            return {
                "ticket_id": ticket.id,
                "status": ticket.status,
                "category": ticket.category,
                "priority": ticket.priority,
                "created_at": ticket.created_at.isoformat() if ticket.created_at else None,
            }
        finally:
            if close_db:
                db.close()


# ── Tool 6: get_customer_tickets ─────────────────────────────────────

class GetCustomerTicketsArgs(BaseModel):
    limit: int = Field(10, ge=1, le=50, description="Maximum number of tickets to return (1-50)")


def validate_get_customer_tickets_args(args: dict) -> tuple[bool, GetCustomerTicketsArgs | None, str | None]:
    """Validates raw arguments for get_customer_tickets.

    Args:
        args (dict): Raw arguments dictionary.

    Returns:
        tuple[bool, GetCustomerTicketsArgs | None, str | None]:
            (is_valid, validated_model_instance, error_message_if_invalid)
    """
    if not isinstance(args, dict):
        return False, None, f"Tool arguments must be a JSON object/dict, got {type(args).__name__}"

    try:
        validated_args = GetCustomerTicketsArgs(**args)
        return True, validated_args, None
    except ValidationError as err:
        errors = err.errors()
        err_msg = errors[0].get("msg", str(err))
        field = errors[0].get("loc", ["limit"])[0]
        return False, None, f"Validation Error: {err_msg} for field '{field}'"
    except Exception as err:
        return False, None, f"Validation Error: {err}"


def get_customer_tickets(
    authenticated_customer_id: int,
    limit: int = 10,
    db: Session | None = None
) -> dict:
    """Lists tickets owned by the authenticated customer.

    Only returns tickets where Ticket.customer_id matches the authenticated
    customer. Tickets without a customer_id (legacy) are excluded.

    Args:
        authenticated_customer_id (int): ID of the authenticated customer.
        limit (int): Maximum number of tickets to return.
        db (Session, optional): SQLAlchemy DB session.

    Returns:
        dict: List of tickets owned by this customer.
    """
    with traced_span("tool.get_customer_tickets") as span:
        safe_set_attribute(span, "tool.name", "get_customer_tickets")
        safe_set_attribute(span, "tool.customer_id", authenticated_customer_id)
        safe_set_attribute(span, "tool.limit", limit)

        close_db = False
        if db is None:
            db = SessionLocal()
            close_db = True

        try:
            tickets = (
                db.query(Ticket)
                .filter(Ticket.customer_id == authenticated_customer_id)
                .order_by(Ticket.created_at.desc())
                .limit(limit)
                .all()
            )

            safe_set_attribute(span, "tool.tickets_found", len(tickets))

            return {
                "tickets": [
                    {
                        "ticket_id": ticket.id,
                        "status": ticket.status,
                        "category": ticket.category,
                        "priority": ticket.priority,
                        "created_at": ticket.created_at.isoformat() if ticket.created_at else None,
                    }
                    for ticket in tickets
                ],
                "count": len(tickets),
            }
        finally:
            if close_db:
                db.close()


# ── Tool 7: cancel_order (state-changing) ────────────────────────────


class CancelOrderArgs(BaseModel):
    order_id: int = Field(gt=0, description="Unique positive integer ID of the order to cancel")


def validate_cancel_order_args(args: dict) -> tuple[bool, CancelOrderArgs | None, str | None]:
    """Validates raw arguments for cancel_order.

    Args:
        args (dict): Raw arguments dictionary.

    Returns:
        tuple[bool, CancelOrderArgs | None, str | None]:
            (is_valid, validated_model_instance, error_message_if_invalid)
    """
    if not isinstance(args, dict):
        return False, None, f"Tool arguments must be a JSON object/dict, got {type(args).__name__}"

    if "order_id" in args and isinstance(args["order_id"], bool):
        return False, None, "Validation Error: order_id cannot be a boolean value"

    try:
        validated_args = CancelOrderArgs(**args)
        return True, validated_args, None
    except ValidationError as err:
        errors = err.errors()
        err_msg = errors[0].get("msg", str(err))
        field = errors[0].get("loc", ["order_id"])[0]
        return False, None, f"Validation Error: {err_msg} for field '{field}'"
    except Exception as err:
        return False, None, f"Validation Error: {err}"


def cancel_order(
    order_id: int,
    authenticated_customer_id: int,
    db: Session | None = None
) -> dict:
    """Cancels an eligible order by updating its status to CANCELLED in PostgreSQL.

    Reuses CANCELLATION_ELIGIBLE_STATUSES business rule constant.

    Order of checks:
    1. Order must exist.
    2. Authenticated customer must own the order.
    3. Order status must be cancellation-eligible (PROCESSING or SHIPPED).
    4. Idempotency: if already CANCELLED, return current state without error.
    5. Update order status to CANCELLED and record the action.

    Args:
        order_id (int): ID of the order to cancel.
        authenticated_customer_id (int): ID of the authenticated customer.
        db (Session, optional): SQLAlchemy DB session.

    Returns:
        dict: Cancellation result with order_id, status, and action_id, or a structured error.
    """
    with traced_span("tool.cancel_order") as span:
        safe_set_attribute(span, "tool.name", "cancel_order")
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
                return {
                    "error": (
                        f"Unauthorized: Customer #{authenticated_customer_id} does not "
                        f"have permission to access Order #{order_id}"
                    )
                }

            safe_set_attribute(span, "tool.authorized", True)
            safe_set_attribute(span, "tool.order_status", order.status)

            # 3. Idempotency: if already CANCELLED, return current state cleanly
            if order.status == "CANCELLED":
                safe_set_attribute(span, "tool.already_cancelled", True)
                return {
                    "status": "ALREADY_CANCELLED",
                    "message": f"Order #{order_id} is already cancelled.",
                    "order_id": order_id,
                    "order_status": order.status,
                }

            # 4. Eligibility check — reuse shared constant
            if order.status not in CANCELLATION_ELIGIBLE_STATUSES:
                safe_set_attribute(span, "tool.eligible", False)
                return {
                    "error": (
                        f"Order #{order_id} cannot be cancelled. "
                        f"Current status is {order.status}. "
                        f"Only orders in {sorted(CANCELLATION_ELIGIBLE_STATUSES)} status can be cancelled."
                    )
                }

            safe_set_attribute(span, "tool.eligible", True)

            # 5. Check for duplicate cancellation action
            existing_action = (
                db.query(Action)
                .filter(
                    Action.action_type == "CANCELLATION",
                    Action.reference_id == order_id,
                    Action.status == "COMPLETED",
                )
                .first()
            )
            if existing_action:
                safe_set_attribute(span, "tool.duplicate_found", True)
                return {
                    "status": "ALREADY_CANCELLED",
                    "message": f"Cancellation was already recorded for Order #{order_id}.",
                    "order_id": order_id,
                    "order_status": order.status,
                    "action_id": existing_action.id,
                }

            safe_set_attribute(span, "tool.duplicate_found", False)

            # 6. Execute cancellation — update order status in PostgreSQL
            previous_status = order.status
            order.status = "CANCELLED"

            action = Action(
                action_type="CANCELLATION",
                reference_id=order_id,
                customer_id=authenticated_customer_id,
                amount=None,
                status="COMPLETED",
            )
            db.add(action)
            db.commit()
            db.refresh(order)
            db.refresh(action)

            safe_set_attribute(span, "tool.action_id", action.id)
            safe_set_attribute(span, "tool.previous_status", previous_status)
            safe_set_attribute(span, "tool.new_status", order.status)

            return {
                "status": "COMPLETED",
                "message": f"Order #{order_id} has been successfully cancelled.",
                "order_id": order_id,
                "previous_status": previous_status,
                "order_status": order.status,
                "action_id": action.id,
            }
        finally:
            if close_db:
                db.close()
