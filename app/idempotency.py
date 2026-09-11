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


def compute_request_hash(params: dict) -> str:
    """Computes a deterministic SHA-256 hex digest of sorted dictionary parameters."""
    serialized = json.dumps(params, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def is_transient_error(err: Exception) -> bool:
    """Classifies whether an exception is a transient, retryable failure."""
    if isinstance(err, TransientError):
        return True
    if isinstance(err, (ConnectionError, TimeoutError, OSError)):
        return True
    err_str = str(err).lower()
    transient_keywords = [
        "timeout", "connection refused", "connection reset", "503", "502", "504",
        "service unavailable", "rate limit", "temporarily unavailable", "transient"
    ]
    return any(kw in err_str for kw in transient_keywords)


def is_permanent_error(err: Exception) -> bool:
    """Classifies whether an exception is a permanent, non-retryable failure."""
    if isinstance(err, PermanentError):
        return True
    if isinstance(err, (HTTPException, PermissionError)):
        return True
    err_str = str(err).lower()
    permanent_keywords = [
        "unauthorized", "forbidden", "not found", "invalid", "malformed",
        "conflict", "rejected", "permission denied"
    ]
    return any(kw in err_str for kw in permanent_keywords)


def get_updated_response_data(response_data: dict, db: Session) -> dict:
    """Refreshes stored response data if a linked approval was resolved in the DB."""
    if not isinstance(response_data, dict):
        return response_data

    approval_id = response_data.get("approval_id")
    if not approval_id and isinstance(response_data.get("tool_result"), dict):
        approval_id = response_data["tool_result"].get("approval_id")

    if approval_id:
        approval = db.query(Approval).filter(Approval.id == approval_id).first()
        if approval:
            updated = dict(response_data)
            if approval.status == "PENDING":
                updated["approval_status"] = "PENDING_APPROVAL"
                updated["status"] = "PENDING_APPROVAL"
            elif approval.status == "APPROVED":
                action = db.query(Action).filter(Action.id == approval.action_id).first()
                updated["status"] = "COMPLETED"
                updated["approval_status"] = "APPROVED"
                if action:
                    updated["action_status"] = action.status
                    updated["message"] = f"Refund request of ${action.amount:.2f} for Order #{action.reference_id} was APPROVED and COMPLETED."
            elif approval.status == "REJECTED":
                updated["status"] = "REJECTED"
                updated["approval_status"] = "REJECTED"
                updated["message"] = "Refund request was REJECTED by administrator."
            return updated
    return response_data


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
    if not idempotency_key:
        result = action_fn()
        return result, False

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
            if existing.request_hash != req_hash:
                logger.warning(f"Payload mismatch for idempotency_key='{idempotency_key}' (customer_id={customer_id})")
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Idempotency key '{idempotency_key}' conflict: Request payload does not match original request.",
                )

            # If existing is COMPLETED or PENDING_APPROVAL: return replayed response
            if existing.status in ("COMPLETED", "PENDING_APPROVAL"):
                res_data = get_updated_response_data(existing.response_data or {}, db)
                return res_data, True

            # If existing is FAILED or REJECTED: return permanent error response
            if existing.status in ("FAILED", "REJECTED"):
                res_data = get_updated_response_data(existing.response_data or {
                    "error": existing.last_error or f"Operation is in permanent failure state '{existing.status}'.",
                    "status": existing.status,
                    "retry_count": existing.retry_count,
                }, db)
                return res_data, True

            # If existing is RETRYABLE_FAILED: trigger retry logic!
            if existing.status in ("RETRYABLE_FAILED", "PENDING"):
                return retry_idempotent_operation(
                    customer_id=customer_id,
                    idempotency_key=idempotency_key,
                    action_fn=action_fn,
                    db=db,
                    max_retries=max_retries,
                )

        # 2. Reserve key in DB with PENDING status BEFORE calling action_fn
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
        except IntegrityError:
            # DB UniqueConstraint caught a concurrent request collision!
            db.rollback()
            winner = db.query(IdempotencyRecord).filter(
                IdempotencyRecord.customer_id == customer_id,
                IdempotencyRecord.idempotency_key == idempotency_key,
            ).first()

            if winner:
                if winner.request_hash != req_hash:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"Idempotency key '{idempotency_key}' conflict: Request payload does not match original request.",
                    )
                # Wait briefly for winner to finish executing
                for _ in range(30):
                    if winner.status != "PENDING" or winner.response_data is not None:
                        break
                    time.sleep(0.1)
                    db.refresh(winner)

                res_data = get_updated_response_data(winner.response_data or {}, db)
                return res_data, True
            raise

        # 3. Execute business operation callback
        try:
            result = action_fn()
        except Exception as err:
            if is_transient_error(err):
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

            approval_status = result.get("approval_status") or (
                result.get("tool_result", {}).get("status") if isinstance(result.get("tool_result"), dict) else None
            )
            if approval_status == "PENDING_APPROVAL":
                pending_record.status = "PENDING_APPROVAL"
                pending_record.response_data = result
                db.commit()
                logger.info(f"Operation key='{idempotency_key}' transitioned to PENDING_APPROVAL")
                return result, False

            if "error" in result:
                err_msg = str(result["error"])
                if is_permanent_error(ValueError(err_msg)) or "Unauthorized" in err_msg or "Permission" in err_msg:
                    pending_record.status = "FAILED"
                    pending_record.last_error = err_msg
                    pending_record.response_data = result
                    db.commit()
                    logger.error(f"Permanent error returned for key='{idempotency_key}': {err_msg}")
                    return result, False
                else:
                    pending_record.status = "RETRYABLE_FAILED" if pending_record.retry_count < pending_record.max_retries else "FAILED"
                    pending_record.last_error = err_msg
                    pending_record.response_data = result
                    db.commit()
                    logger.warning(f"Transient error returned for key='{idempotency_key}': {err_msg}")
                    return result, False

        # 4. Update reservation with completed result
        pending_record.status = "COMPLETED"
        pending_record.response_data = result
        db.commit()
        db.refresh(pending_record)
        logger.info(f"Operation key='{idempotency_key}' completed successfully.")

        return result, False

    finally:
        if close_db:
            db.close()


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
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No idempotency record found for key '{idempotency_key}'.",
            )

        logger.info(
            f"Retry requested for key='{idempotency_key}', customer_id={customer_id}, "
            f"status='{existing.status}', retry_count={existing.retry_count}/{existing.max_retries}"
        )

        # 1. If COMPLETED: return stored response (replayed)
        if existing.status == "COMPLETED":
            res_data = get_updated_response_data(existing.response_data or {}, db)
            return res_data, True

        # 2. If FAILED or REJECTED: permanent state, do NOT retry
        if existing.status in ("FAILED", "REJECTED"):
            res_data = dict(existing.response_data or {})
            if "error" not in res_data or not res_data["error"]:
                res_data["error"] = existing.last_error or f"Operation is in permanent failure state '{existing.status}'."
            res_data["status"] = existing.status
            res_data["retry_count"] = existing.retry_count
            res_data["max_retries"] = existing.max_retries
            return res_data, True

        # 3. If PENDING_APPROVAL: check approval state
        if existing.status == "PENDING_APPROVAL":
            res_data = get_updated_response_data(existing.response_data or {}, db)
            if res_data.get("approval_status") == "APPROVED" or res_data.get("status") == "COMPLETED":
                existing.status = "COMPLETED"
                existing.response_data = res_data
                db.commit()
                return res_data, True
            elif res_data.get("approval_status") == "REJECTED" or res_data.get("status") == "REJECTED":
                existing.status = "REJECTED"
                existing.response_data = res_data
                db.commit()
                return res_data, True
            else:
                # Still PENDING_APPROVAL! Do NOT retry as error!
                return res_data, True

        # 4. Check if max retries exceeded
        if existing.retry_count >= existing.max_retries:
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
            existing.retry_count += 1
            existing.status = "PROCESSING"
            db.commit()

            try:
                result = action_fn()
            except Exception as err:
                if is_transient_error(err):
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

                approval_status = result.get("approval_status") or (
                    result.get("tool_result", {}).get("status") if isinstance(result.get("tool_result"), dict) else None
                )
                if approval_status == "PENDING_APPROVAL":
                    existing.status = "PENDING_APPROVAL"
                    existing.response_data = result
                    db.commit()
                    return result, True

                if "error" in result:
                    err_msg = str(result["error"])
                    if is_permanent_error(ValueError(err_msg)) or "Unauthorized" in err_msg or "Permission" in err_msg:
                        existing.status = "FAILED"
                    else:
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
