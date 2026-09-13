"""Approval service — create, approve, and reject approval requests.

This module manipulates database state only. It does NOT execute
the actual business action (replacement, refund, etc.).
"""
from datetime import datetime

from sqlalchemy.orm import Session

from app.database import SessionLocal, Base, engine
from app.models import Action, Approval
from app.observability import traced_span, safe_set_attribute

# Ensure tables exist
Base.metadata.create_all(bind=engine)

# Valid approval status values
APPROVAL_PENDING = "PENDING"
APPROVAL_APPROVED = "APPROVED"
APPROVAL_REJECTED = "REJECTED"


def create_approval_request(
    action_type: str,
    reference_id: int,
    customer_id: int,
    amount: float | None = None,
    db: Session | None = None,
) -> dict:
    """Creates an Action and a linked PENDING Approval record.

    Args:
        action_type (str): Type of action (e.g. "REPLACEMENT_REQUEST", "REFUND").
        reference_id (int): ID of the related entity (e.g. order_id).
        customer_id (int): ID of the customer requesting the action.
        amount (float, optional): Monetary amount involved (e.g. refund amount).
        db (Session, optional): SQLAlchemy session.

    Returns:
        dict with action_id, approval_id, and status.
    """
    with traced_span("approval.create_approval_request") as span:
        safe_set_attribute(span, "approval.action_type", action_type)
        safe_set_attribute(span, "approval.reference_id", reference_id)
        if amount is not None:
            safe_set_attribute(span, "approval.amount", amount)

        close_db = False
        if db is None:
            db = SessionLocal()
            close_db = True

        try:
            action = Action(
                action_type=action_type,
                reference_id=reference_id,
                customer_id=customer_id,
                amount=amount,
                status="PENDING",
            )
            db.add(action)
            db.flush()  # get action.id before creating approval

            approval = Approval(
                action_id=action.id,
                status=APPROVAL_PENDING,
            )
            db.add(approval)
            db.commit()
            db.refresh(action)
            db.refresh(approval)

            safe_set_attribute(span, "approval.action_id", action.id)
            safe_set_attribute(span, "approval.approval_id", approval.id)
            safe_set_attribute(span, "approval.status", approval.status)

            return {
                "action_id": action.id,
                "action_type": action.action_type,
                "reference_id": action.reference_id,
                "customer_id": action.customer_id,
                "amount": action.amount,
                "action_status": action.status,
                "approval_id": approval.id,
                "status": approval.status,
            }
        finally:
            if close_db:
                db.close()


def approve_request(
    approval_id: int,
    resolved_by: str,
    reason: str | None = None,
    db: Session | None = None,
) -> dict:
    """Transitions a PENDING approval to APPROVED and executes the linked action.

    Args:
        approval_id (int): ID of the approval record.
        resolved_by (str): Identifier of the person approving.
        reason (str, optional): Reason for approval.
        db (Session, optional): SQLAlchemy session.

    Returns:
        dict with approval and action execution details or an error.
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        approval = db.query(Approval).filter(Approval.id == approval_id).first()
        if not approval:
            return {"error": f"Approval #{approval_id} not found"}

        if approval.status != APPROVAL_PENDING:
            return {
                "error": (
                    f"Cannot approve: Approval #{approval_id} is already "
                    f"{approval.status} and cannot be changed"
                ),
                "current_status": approval.status,
            }

        approval.status = APPROVAL_APPROVED
        approval.resolved_at = datetime.utcnow()
        approval.resolved_by = resolved_by
        approval.reason = reason

        # Transition linked Action to COMPLETED (execute synthetic action)
        action = db.query(Action).filter(Action.id == approval.action_id).first()
        if action:
            action.status = "COMPLETED"

        db.commit()
        db.refresh(approval)
        if action:
            db.refresh(action)

        return {
            "approval_id": approval.id,
            "action_id": approval.action_id,
            "approval_status": approval.status,
            "action_status": action.status if action else None,
            "action_type": action.action_type if action else None,
            "reference_id": action.reference_id if action else None,
            "customer_id": action.customer_id if action else None,
            "amount": action.amount if action else None,
            "resolved_by": approval.resolved_by,
            "resolved_at": str(approval.resolved_at),
            "reason": approval.reason,
            "executed": True,
        }
    finally:
        if close_db:
            db.close()


def reject_request(
    approval_id: int,
    resolved_by: str,
    reason: str | None = None,
    db: Session | None = None,
) -> dict:
    """Transitions a PENDING approval to REJECTED and updates the action state without executing.

    Args:
        approval_id (int): ID of the approval record.
        resolved_by (str): Identifier of the person rejecting.
        reason (str, optional): Reason for rejection.
        db (Session, optional): SQLAlchemy session.

    Returns:
        dict with approval details or an error.
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        approval = db.query(Approval).filter(Approval.id == approval_id).first()
        if not approval:
            return {"error": f"Approval #{approval_id} not found"}

        if approval.status != APPROVAL_PENDING:
            return {
                "error": (
                    f"Cannot reject: Approval #{approval_id} is already "
                    f"{approval.status} and cannot be changed"
                ),
                "current_status": approval.status,
            }

        approval.status = APPROVAL_REJECTED
        approval.resolved_at = datetime.utcnow()
        approval.resolved_by = resolved_by
        approval.reason = reason

        # Transition linked Action to REJECTED (action will NOT execute)
        action = db.query(Action).filter(Action.id == approval.action_id).first()
        if action:
            action.status = "REJECTED"

        db.commit()
        db.refresh(approval)
        if action:
            db.refresh(action)

        return {
            "approval_id": approval.id,
            "action_id": approval.action_id,
            "approval_status": approval.status,
            "action_status": action.status if action else None,
            "action_type": action.action_type if action else None,
            "reference_id": action.reference_id if action else None,
            "customer_id": action.customer_id if action else None,
            "amount": action.amount if action else None,
            "resolved_by": approval.resolved_by,
            "resolved_at": str(approval.resolved_at),
            "reason": approval.reason,
            "executed": False,
        }
    finally:
        if close_db:
            db.close()

