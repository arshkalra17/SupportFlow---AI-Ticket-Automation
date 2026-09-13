"""Idempotency and Failure Recovery management module for SupportFlow.

Provides SHA-256 request fingerprinting, database persistence, HTTP 409 conflict detection,
concurrency-safe DB unique constraint race-condition handling, transient failure classification,
and bounded retry mechanisms for state-changing operations.
"""

import hashlib
import json
import logging
import time
from datetime import datetime
from typing import Callable, Any
from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import IdempotencyRecord, Approval, Action
from app.observability import traced_span, hash_sensitive, safe_set_attribute

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("supportflow.idempotency")


class TransientError(Exception):
    """Temporary infrastructure/provider failure that is safe to retry."""
    pass


class PermanentError(Exception):
    """Permanent authorization/validation/business failure that must NOT be retried."""
    pass


@traced_span("idempotency.compute_request_hash")
def compute_request_hash(params: dict) -> str:
    """Computes a deterministic SHA-256 hex digest of sorted dictionary parameters."""
    from opentelemetry import trace
    span = trace.get_current_span()
    try:
        serialized = json.dumps(params, sort_keys=True)
        req_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        safe_set_attribute(span, "idempotency.request_hash", hash_sensitive(req_hash))
        return req_hash
    except Exception:
        return hashlib.sha256(json.dumps(params, sort_keys=True).encode("utf-8")).hexdigest()


@traced_span("idempotency.is_transient_error")
def is_transient_error(err: Exception) -> bool:
    """Classifies whether an exception is a transient, retryable failure."""
    from opentelemetry import trace
    span = trace.get_current_span()
    try:
        if isinstance(err, TransientError):
            safe_set_attribute(span, "error.classification", "transient")
            safe_set_attribute(span, "error.type", type(err).__name__)
            return True
        if isinstance(err, (ConnectionError, TimeoutError, OSError)):
            safe_set_attribute(span, "error.classification", "transient")
            safe_set_attribute(span, "error.type", type(err).__name__)
            return True
        err_str = str(err).lower()
        transient_keywords = [
            "timeout", "connection refused", "connection reset", "503", "502", "504",
            "service unavailable", "rate limit", "temporarily unavailable", "transient"
        ]
        result = any(kw in err_str for kw in transient_keywords)
        safe_set_attribute(span, "error.classification", "transient" if result else "unknown")
        safe_set_attribute(span, "error.type", type(err).__name__)
        return result
    except Exception:
        return False


@traced_span("idempotency.is_permanent_error")
def is_permanent_error(err: Exception) -> bool:
    """Classifies whether an exception is a permanent, non-retryable failure."""
    from opentelemetry import trace
    span = trace.get_current_span()
    try:
        if isinstance(err, PermanentError):
            safe_set_attribute(span, "error.classification", "permanent")
            safe_set_attribute(span, "error.type", type(err).__name__)
            return True
        if isinstance(err, (HTTPException, PermissionError)):
            safe_set_attribute(span, "error.classification", "permanent")
            safe_set_attribute(span, "error.type", type(err).__name__)
            return True
        err_str = str(err).lower()
        permanent_keywords = [
            "unauthorized", "forbidden", "not found", "invalid", "malformed",
            "conflict", "rejected", "permission denied"
        ]
        result = any(kw in err_str for kw in permanent_keywords)
        safe_set_attribute(span, "error.classification", "permanent" if result else "unknown")
        safe_set_attribute(span, "error.type", type(err).__name__)
        return result
    except Exception:
        return False


@traced_span("idempotency.get_updated_response_data")
def get_updated_response_data(response_data: dict, db: Session) -> dict:
    """Refreshes stored response data if a linked approval was resolved in the DB."""
    from opentelemetry import trace
    span = trace.get_current_span()
    try:
        if not isinstance(response_data, dict):
            return response_data

        approval_id = response_data.get("approval_id")
        if not approval_id and isinstance(response_data.get("tool_result"), dict):
            approval_id = response_data["tool_result"].get("approval_id")

        if approval_id:
            safe_set_attribute(span, "idempotency.approval_id", approval_id)
            approval = db.query(Approval).filter(Approval.id == approval_id).first()
            if approval:
                safe_set_attribute(span, "idempotency.approval_status", approval.status)
                updated = dict(response_data)
                if approval.status == "PENDING":
                    updated["approval_status"] = "PENDING_APPROVAL"
                    updated["status"] = "PENDING_APPROVAL"
                elif approval.status == "APPROVED":
                    action = db.query(Action).filter(Action.id == approval.action_id).first()
                    updated["status"] = "COMPLETED"
                    updated["approval_status"] = "APPROVED"
                    if action:
                        safe_set_attribute(span, "idempotency.action_status", action.status)
                        updated["action_status"] = action.status
                        updated["message"] = f"Refund request of ${action.amount:.2f} for Order #{action.reference_id} was APPROVED and COMPLETED."
                elif approval.status == "REJECTED":
                    updated["status"] = "REJECTED"
                    updated["approval_status"] = "REJECTED"
                    updated["message"] = "Refund request was REJECTED by administrator."
                return updated
        return response_data
    except Exception:
        return response_data


@traced_span("idempotency.execute_with_idempotency")
def execute_with_idempotency(
    customer_id: int,
    idempotency_key: str | None,
    operation_type: str,
    request_params: dict,
    action_fn: Callable[[], dict],
    db: Session | None = None,
    max_retries: int = 3,
) -> tuple[dict, bool]:
    """Executes state-changing operation with idempotency and retry classification.

    Concurrency protection:
    Uses PostgreSQL UniqueConstraint on (customer_id, idempotency_key).
    Reserves a PENDING record in the database BEFORE calling action_fn(),
    guaranteeing that concurrent duplicate requests produce exactly ONE business action.

    Args:
        customer_id (int): Trusted customer ID.
        idempotency_key (str | None): Optional idempotency key from header/context.
        operation_type (str): Name of operation (e.g. "REFUND", "REPLACEMENT_REQUEST").
        request_params (dict): Request parameters to fingerprint.
        action_fn (Callable): Callback that performs the actual business operation.
        db (Session, optional): SQLAlchemy DB session.
        max_retries (int): Bounded retry limit.

    Returns:
        tuple[dict, bool]: (result_dict, is_replayed)

    Raises:
        HTTPException 409: If idempotency key is reused with a different request fingerprint.
    """
    from opentelemetry import trace
    span = trace.get_current_span()

    safe_set_attribute(span, "idempotency.customer_id", customer_id)
    safe_set_attribute(span, "idempotency.operation_type", operation_type)
    safe_set_attribute(span, "idempotency.max_retries", max_retries)
    if idempotency_key:
        safe_set_attribute(span, "idempotency.key_hash", hash_sensitive(idempotency_key))

    if not idempotency_key:
        safe_set_attribute(span, "idempotency.enabled", False)
        result = action_fn()
        return result, False

    safe_set_attribute(span, "idempotency.enabled", True)
    req_hash = compute_request_hash(request_params)

    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        # 1. Lookup existing record in PostgreSQL
        existing = db.query(IdempotencyRecord).filter(
            IdempotencyRecord.customer_id == customer_id,
            IdempotencyRecord.idempotency_key == idempotency_key,
        ).first()

        if existing:
            # Check request fingerprint match
            safe_set_attribute(span, "idempotency.record_found", True)
            safe_set_attribute(span, "idempotency.existing_status", existing.status)
            safe_set_attribute(span, "idempotency.retry_count", existing.retry_count)
            if existing.request_hash != req_hash:
                safe_set_attribute(span, "idempotency.conflict", True)
                logger.warning(f"Payload mismatch for idempotency_key='{idempotency_key}' (customer_id={customer_id})")
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Idempotency key '{idempotency_key}' conflict: Request payload does not match original request.",
                )

            # If existing is COMPLETED or PENDING_APPROVAL: return replayed response
            if existing.status in ("COMPLETED", "PENDING_APPROVAL"):
                safe_set_attribute(span, "idempotency.replayed", True)
                res_data = get_updated_response_data(existing.response_data or {}, db)
                return res_data, True

            # If existing is FAILED or REJECTED: return permanent error response
            if existing.status in ("FAILED", "REJECTED"):
                safe_set_attribute(span, "idempotency.replayed", True)
                safe_set_attribute(span, "idempotency.permanent_failure", True)
                res_data = get_updated_response_data(existing.response_data or {
                    "error": existing.last_error or f"Operation is in permanent failure state '{existing.status}'.",
                    "status": existing.status,
                    "retry_count": existing.retry_count,
                }, db)
                return res_data, True

            # If existing is PENDING: another request is actively executing.
            # Wait for it to reach a terminal state, then replay its result.
            # Do NOT call retry_idempotent_operation — that would re-execute
            # action_fn, defeating the exactly-once guarantee.
            if existing.status == "PENDING":
                safe_set_attribute(span, "idempotency.concurrent_wait", True)
                for _ in range(50):
                    if existing.status != "PENDING":
                        break
                    time.sleep(0.1)
                    db.refresh(existing)
                logger.info(
                    f"Concurrent PENDING wait complete for key='{idempotency_key}': "
                    f"resolved to status='{existing.status}'"
                )
                safe_set_attribute(span, "idempotency.resolved_status", existing.status)
                res_data = get_updated_response_data(existing.response_data or {}, db)
                return res_data, True

            # If existing is RETRYABLE_FAILED: a previous attempt failed transiently.
            # Retry is appropriate here — the original request has already ended.
            if existing.status == "RETRYABLE_FAILED":
                safe_set_attribute(span, "idempotency.retry_triggered", True)
                return retry_idempotent_operation(
                    customer_id=customer_id,
                    idempotency_key=idempotency_key,
                    action_fn=action_fn,
                    db=db,
                    max_retries=max_retries,
                )

        # 2. Reserve key in DB with PENDING status BEFORE calling action_fn
        safe_set_attribute(span, "idempotency.record_found", False)
        safe_set_attribute(span, "idempotency.creating_record", True)
        pending_record = IdempotencyRecord(
            customer_id=customer_id,
            idempotency_key=idempotency_key,
            operation_type=operation_type,
            request_hash=req_hash,
            status="PENDING",
            response_data=None,
            retry_count=0,
            max_retries=max_retries,
        )
        db.add(pending_record)
        try:
            db.commit()
            db.refresh(pending_record)
            safe_set_attribute(span, "idempotency.record_created", True)
        except IntegrityError:
            # DB UniqueConstraint caught a concurrent request collision!
            safe_set_attribute(span, "idempotency.race_condition", True)
            db.rollback()
            winner = db.query(IdempotencyRecord).filter(
                IdempotencyRecord.customer_id == customer_id,
                IdempotencyRecord.idempotency_key == idempotency_key,
            ).first()

            if winner:
                if winner.request_hash != req_hash:
                    safe_set_attribute(span, "idempotency.conflict", True)
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"Idempotency key '{idempotency_key}' conflict: Request payload does not match original request.",
                    )
                # Wait briefly for winner to finish executing
                safe_set_attribute(span, "idempotency.waiting_for_winner", True)
                for _ in range(30):
                    if winner.status != "PENDING" or winner.response_data is not None:
                        break
                    time.sleep(0.1)
                    db.refresh(winner)

                safe_set_attribute(span, "idempotency.replayed", True)
                res_data = get_updated_response_data(winner.response_data or {}, db)
                return res_data, True
            raise

        # 3. Execute business operation callback
        safe_set_attribute(span, "idempotency.executing_action", True)
        try:
            result = action_fn()
            safe_set_attribute(span, "idempotency.action_success", True)
            safe_set_attribute(span, "idempotency.action_success", True)
        except Exception as err:
            safe_set_attribute(span, "idempotency.action_error", True)
            safe_set_attribute(span, "error.type", type(err).__name__)
            if is_transient_error(err):
                safe_set_attribute(span, "error.classification", "transient")
                pending_record.status = "RETRYABLE_FAILED" if pending_record.retry_count < pending_record.max_retries else "FAILED"
                pending_record.last_error = str(err)
                err_dict = {
                    "error": str(err),
                    "status": pending_record.status,
                    "retry_count": pending_record.retry_count,
                    "max_retries": pending_record.max_retries,
                }
                pending_record.response_data = err_dict
                db.commit()
                logger.warning(f"Transient failure for idempotency_key='{idempotency_key}' (attempt 0): {err}")
                return err_dict, False
            else:
                safe_set_attribute(span, "error.classification", "permanent")
                pending_record.status = "FAILED"
                pending_record.last_error = str(err)
                err_dict = {
                    "error": str(err),
                    "status": "FAILED",
                    "retry_count": pending_record.retry_count,
                    "max_retries": pending_record.max_retries,
                }
                pending_record.response_data = err_dict
                db.commit()
                logger.error(f"Permanent failure for idempotency_key='{idempotency_key}': {err}")
                return err_dict, False

        # If result is dict, inspect for action_id, approval_status, or authorization error
        if isinstance(result, dict):
            action_id = result.get("action_id")
            if not action_id and isinstance(result.get("tool_result"), dict):
                action_id = result["tool_result"].get("action_id")
            if action_id and not pending_record.action_id:
                pending_record.action_id = action_id
                safe_set_attribute(span, "idempotency.action_id", action_id)

            approval_status = result.get("approval_status") or (
                result.get("tool_result", {}).get("status") if isinstance(result.get("tool_result"), dict) else None
            )
            if approval_status == "PENDING_APPROVAL":
                safe_set_attribute(span, "idempotency.pending_approval", True)
                pending_record.status = "PENDING_APPROVAL"
                pending_record.response_data = result
                db.commit()
                logger.info(f"Operation key='{idempotency_key}' transitioned to PENDING_APPROVAL")
                return result, False

            if result.get("error"):
                err_msg = str(result["error"])
                safe_set_attribute(span, "idempotency.result_has_error", True)
                if is_permanent_error(ValueError(err_msg)) or "Unauthorized" in err_msg or "Permission" in err_msg:
                    safe_set_attribute(span, "error.classification", "permanent")
                    pending_record.status = "FAILED"
                    pending_record.last_error = err_msg
                    pending_record.response_data = result
                    db.commit()
                    logger.error(f"Permanent error returned for key='{idempotency_key}': {err_msg}")
                    return result, False
                else:
                    safe_set_attribute(span, "error.classification", "transient")
                    pending_record.status = "RETRYABLE_FAILED" if pending_record.retry_count < pending_record.max_retries else "FAILED"
                    pending_record.last_error = err_msg
                    pending_record.response_data = result
                    db.commit()
                    logger.warning(f"Transient error returned for key='{idempotency_key}': {err_msg}")
                    return result, False

        # 4. Update reservation with completed result
        safe_set_attribute(span, "idempotency.final_status", "COMPLETED")
        pending_record.status = "COMPLETED"
        pending_record.response_data = result
        db.commit()
        db.refresh(pending_record)
        logger.info(f"Operation key='{idempotency_key}' completed successfully.")

        return result, False

    finally:
        if close_db:
            db.close()


@traced_span("idempotency.retry_idempotent_operation")
def retry_idempotent_operation(
    customer_id: int,
    idempotency_key: str,
    action_fn: Callable[[], dict] | None = None,
    db: Session | None = None,
    max_retries: int = 3,
) -> tuple[dict, bool]:
    """Retries a previously failed or approved idempotent operation.

    Rules:
    - Retries only RETRYABLE_FAILED or PENDING operations (or APPROVED refunds that failed execution).
    - Reuses existing IdempotencyRecord and Action ID (does NOT create a new logical operation).
    - Enforces bounded retry policy (max_retries).
    - Prevents retrying COMPLETED, FAILED, or REJECTED operations.
    - Prevents retrying PENDING_APPROVAL operations while still pending.

    Returns:
        tuple[dict, bool]: (result_dict, is_replayed)
    """
    from opentelemetry import trace
    span = trace.get_current_span()

    safe_set_attribute(span, "idempotency.customer_id", customer_id)
    safe_set_attribute(span, "idempotency.key_hash", hash_sensitive(idempotency_key))
    safe_set_attribute(span, "idempotency.max_retries", max_retries)

    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        existing = db.query(IdempotencyRecord).filter(
            IdempotencyRecord.customer_id == customer_id,
            IdempotencyRecord.idempotency_key == idempotency_key,
        ).first()

        if not existing:
            safe_set_attribute(span, "idempotency.record_found", False)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No idempotency record found for key '{idempotency_key}'.",
            )

        safe_set_attribute(span, "idempotency.record_found", True)
        safe_set_attribute(span, "idempotency.existing_status", existing.status)
        safe_set_attribute(span, "idempotency.retry_count", existing.retry_count)

        logger.info(
            f"Retry requested for key='{idempotency_key}', customer_id={customer_id}, "
            f"status='{existing.status}', retry_count={existing.retry_count}/{existing.max_retries}"
        )

        # 1. If COMPLETED: return stored response (replayed)
        if existing.status == "COMPLETED":
            safe_set_attribute(span, "idempotency.replayed", True)
            safe_set_attribute(span, "idempotency.replay_reason", "already_completed")
            res_data = get_updated_response_data(existing.response_data or {}, db)
            return res_data, True

        # 2. If FAILED or REJECTED: permanent state, do NOT retry
        if existing.status in ("FAILED", "REJECTED"):
            safe_set_attribute(span, "idempotency.replayed", True)
            safe_set_attribute(span, "idempotency.replay_reason", "permanent_failure")
            res_data = dict(existing.response_data or {})
            if "error" not in res_data or not res_data["error"]:
                res_data["error"] = existing.last_error or f"Operation is in permanent failure state '{existing.status}'."
            res_data["status"] = existing.status
            res_data["retry_count"] = existing.retry_count
            res_data["max_retries"] = existing.max_retries
            return res_data, True

        # 3. If PENDING_APPROVAL: check approval state
        if existing.status == "PENDING_APPROVAL":
            safe_set_attribute(span, "idempotency.pending_approval_check", True)
            res_data = get_updated_response_data(existing.response_data or {}, db)
            if res_data.get("approval_status") == "APPROVED" or res_data.get("status") == "COMPLETED":
                safe_set_attribute(span, "idempotency.approval_resolved", "approved")
                existing.status = "COMPLETED"
                existing.response_data = res_data
                db.commit()
                return res_data, True
            elif res_data.get("approval_status") == "REJECTED" or res_data.get("status") == "REJECTED":
                safe_set_attribute(span, "idempotency.approval_resolved", "rejected")
                existing.status = "REJECTED"
                existing.response_data = res_data
                db.commit()
                return res_data, True
            else:
                # Still PENDING_APPROVAL! Do NOT retry as error!
                safe_set_attribute(span, "idempotency.approval_resolved", "still_pending")
                return res_data, True

        # 4. Check if max retries exceeded
        if existing.retry_count >= existing.max_retries:
            safe_set_attribute(span, "idempotency.max_retries_exceeded", True)
            existing.status = "FAILED"
            existing.last_error = f"Max retries ({existing.max_retries}) exceeded."
            res_data = {
                "error": existing.last_error,
                "status": "FAILED",
                "retry_count": existing.retry_count,
                "max_retries": existing.max_retries,
            }
            existing.response_data = res_data
            db.commit()
            logger.error(f"Operation key='{idempotency_key}' stopped: Max retries ({existing.max_retries}) reached.")
            return res_data, True

        # 5. If action_fn is provided, perform retry execution
        if action_fn is not None:
            safe_set_attribute(span, "idempotency.executing_retry", True)
            existing.retry_count += 1
            safe_set_attribute(span, "idempotency.retry_attempt", existing.retry_count)
            existing.status = "PROCESSING"
            db.commit()

            try:
                result = action_fn()
                safe_set_attribute(span, "idempotency.retry_success", True)
                safe_set_attribute(span, "idempotency.retry_success", True)
            except Exception as err:
                safe_set_attribute(span, "idempotency.retry_error", True)
                safe_set_attribute(span, "error.type", type(err).__name__)
                if is_transient_error(err):
                    safe_set_attribute(span, "error.classification", "transient")
                    if existing.retry_count < existing.max_retries:
                        existing.status = "RETRYABLE_FAILED"
                        existing.last_error = str(err)
                        err_dict = {
                            "error": str(err),
                            "status": existing.status,
                            "retry_count": existing.retry_count,
                            "max_retries": existing.max_retries,
                        }
                    else:
                        existing.status = "FAILED"
                        existing.last_error = f"Max retries ({existing.max_retries}) exceeded: {err}"
                        err_dict = {
                            "error": existing.last_error,
                            "status": existing.status,
                            "retry_count": existing.retry_count,
                            "max_retries": existing.max_retries,
                        }
                    existing.response_data = err_dict
                    db.commit()
                    logger.warning(
                        f"Retry attempt {existing.retry_count} for key='{idempotency_key}' failed (transient): {err}"
                    )
                    return err_dict, True
                else:
                    safe_set_attribute(span, "error.classification", "permanent")
                    existing.status = "FAILED"
                    existing.last_error = str(err)
                    err_dict = {
                        "error": str(err),
                        "status": "FAILED",
                        "retry_count": existing.retry_count,
                        "max_retries": existing.max_retries,
                    }
                    existing.response_data = err_dict
                    db.commit()
                    logger.error(
                        f"Retry attempt {existing.retry_count} for key='{idempotency_key}' failed (permanent): {err}"
                    )
                    return err_dict, True

            # Handle dict response from action_fn
            if isinstance(result, dict):
                action_id = result.get("action_id")
                if not action_id and isinstance(result.get("tool_result"), dict):
                    action_id = result["tool_result"].get("action_id")
                if action_id and not existing.action_id:
                    existing.action_id = action_id
                    safe_set_attribute(span, "idempotency.action_id", action_id)

                approval_status = result.get("approval_status") or (
                    result.get("tool_result", {}).get("status") if isinstance(result.get("tool_result"), dict) else None
                )
                if approval_status == "PENDING_APPROVAL":
                    safe_set_attribute(span, "idempotency.pending_approval", True)
                    existing.status = "PENDING_APPROVAL"
                    existing.response_data = result
                    db.commit()
                    return result, True

                if result.get("error"):
                    err_msg = str(result["error"])
                    safe_set_attribute(span, "idempotency.result_has_error", True)
                    if is_permanent_error(ValueError(err_msg)) or "Unauthorized" in err_msg or "Permission" in err_msg:
                        safe_set_attribute(span, "error.classification", "permanent")
                        existing.status = "FAILED"
                    else:
                        safe_set_attribute(span, "error.classification", "transient")
                        if existing.retry_count < existing.max_retries:
                            existing.status = "RETRYABLE_FAILED"
                        else:
                            existing.status = "FAILED"
                            err_msg = f"Max retries ({existing.max_retries}) exceeded: {err_msg}"
                    existing.last_error = err_msg
                    existing.response_data = result
                    db.commit()
                    return result, True

            # Execution succeeded!
            safe_set_attribute(span, "idempotency.final_status", "COMPLETED")
            existing.status = "COMPLETED"
            existing.response_data = result
            db.commit()
            logger.info(
                f"Retry attempt {existing.retry_count} for key='{idempotency_key}' succeeded -> COMPLETED"
            )
            return result, True

        res_data = get_updated_response_data(existing.response_data or {}, db)
        return res_data, True

    finally:
        if close_db:
            db.close()
