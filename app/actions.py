"""Action executor — the single place where state-changing actions take effect.

Both execution paths go through `run_action()`:
  - auto-approved actions (e.g. a refund under the threshold), and
  - actions released by a human approval.

Business rules are re-checked here at execution time, under a row lock,
because an approval can arrive long after the request was made: the order
may have changed, or another refund may have completed in the meantime.
"""

from sqlalchemy.orm import Session

from app.models import Action, Order
from app.observability import traced_span, safe_set_attribute

ACTION_COMPLETED = "COMPLETED"
ACTION_FAILED = "FAILED"


def _execute_refund(action: Action, db: Session) -> str | None:
    """Re-validates and executes a refund. Returns a failure reason, or None."""
    order = (
        db.query(Order)
        .filter(Order.id == action.reference_id)
        .with_for_update()
        .first()
    )
    if not order:
        return f"Order #{action.reference_id} no longer exists"

    if order.customer_id != action.customer_id:
        return f"Order #{order.id} is not owned by customer #{action.customer_id}"

    if action.amount is None or action.amount <= 0:
        return "Refund amount is missing or invalid"

    if order.total is not None and round(action.amount, 2) > round(order.total, 2):
        return (
            f"Refund amount ${action.amount:.2f} exceeds the total of "
            f"Order #{order.id} (${order.total:.2f})"
        )

    already_refunded = (
        db.query(Action)
        .filter(
            Action.action_type == "REFUND",
            Action.reference_id == order.id,
            Action.status == ACTION_COMPLETED,
            Action.id != action.id,
        )
        .first()
    )
    if already_refunded:
        return f"Order #{order.id} was already refunded (action #{already_refunded.id})"

    # Synthetic execution: no payment provider is integrated, so the
    # completed Action record is the refund.
    return None


EXECUTORS = {
    "REFUND": _execute_refund,
}


def run_action(action: Action, db: Session) -> str | None:
    """Executes `action` and sets its status to COMPLETED or FAILED.

    Does not commit; the caller owns the transaction.

    Returns:
        None on success, otherwise the reason execution was refused.
    """
    with traced_span("action.execute") as span:
        safe_set_attribute(span, "action.id", action.id)
        safe_set_attribute(span, "action.type", action.action_type)

        executor = EXECUTORS.get(action.action_type)
        if executor is None:
            # Fail closed: never mark an action done without an executor.
            failure = f"No executor for action type {action.action_type}"
        else:
            failure = executor(action, db)

        action.status = ACTION_FAILED if failure else ACTION_COMPLETED
        safe_set_attribute(span, "action.status", action.status)
        if failure:
            safe_set_attribute(span, "action.failure_reason", failure)
        return failure
