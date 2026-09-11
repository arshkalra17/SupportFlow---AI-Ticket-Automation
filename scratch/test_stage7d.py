"""Stage 7D Verification: Failure Recovery + Retries

10 test cases exercising:
  1. Successful operation (PENDING → COMPLETED, no retry)
  2. Transient failure & successful retry
  3. Permanent failure (authorization / invalid input, no retry)
  4. Retry limit (MAX_RETRIES = 3 exceeded → FAILED)
  5. Idempotent retry retains same Action ID
  6. High-value refund awaiting approval (PENDING_APPROVAL, no retry)
  7. Approved refund execution recovery (no duplicate action/approval)
  8. Completed operation replay (stored result returned)
  9. Worker transient failure (bounded retry in Redis queue)
 10. Worker permanent failure (job discarded, ticket marked failed)

All tests use mocked/fake callbacks — zero live Groq calls.
"""

import os
import sys
import json
import logging

# Ensure project root is on sys.path so `app.*` imports resolve regardless
# of the working directory used to invoke this script.
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.database import Base, engine, SessionLocal
from app.models import (
    Customer, Order, ReplacementRequest, Action, Approval, IdempotencyRecord, Ticket
)
from app.idempotency import (
    execute_with_idempotency, retry_idempotent_operation,
    TransientError, PermanentError,
)
from app.worker import process_single_job
from app.queue import redis_client, QUEUE_NAME

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_stage7d")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def reset_and_seed_db(db):
    """Drop + recreate every table so the new IdempotencyRecord columns
    (retry_count, max_retries, last_error, next_retry_at, action_id) exist,
    then seed minimal test data."""
    db.close()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    fresh = SessionLocal()

    fresh.add(Customer(id=1, email="cust1@example.com", password_hash="hash"))
    fresh.commit()
    fresh.add(Order(id=1003, customer_id=1, status="DELIVERED"))
    fresh.commit()
    fresh.add(Ticket(id=101, customer_message="Help with refund", status="PENDING"))
    fresh.commit()
    return fresh


# ---------------------------------------------------------------------------
# Main test runner
# ---------------------------------------------------------------------------

def run_tests():
    logger.info("Starting Stage 7D Reliability & Retry Verification...")
    db = SessionLocal()
    db = reset_and_seed_db(db)

    # -----------------------------------------------------------------
    # CASE 1 — Successful Operation (PENDING → COMPLETED, No Retry)
    # -----------------------------------------------------------------
    logger.info("\n--- CASE 1: Successful Operation ---")
    action_executed_c1 = 0

    def success_action():
        nonlocal action_executed_c1
        action_executed_c1 += 1
        return {"status": "SUCCESS", "message": "Operation completed"}

    res1, replayed1 = execute_with_idempotency(
        customer_id=1,
        idempotency_key="key-c1-success",
        operation_type="TEST_OP",
        request_params={"action": "test1"},
        action_fn=success_action,
        db=db,
    )
    assert not replayed1, "First call should not be replayed"
    assert res1["status"] == "SUCCESS"
    assert action_executed_c1 == 1, "Action function should execute exactly once"

    rec1 = db.query(IdempotencyRecord).filter_by(idempotency_key="key-c1-success").first()
    assert rec1.status == "COMPLETED", f"Expected COMPLETED, got {rec1.status}"
    assert rec1.retry_count == 0
    logger.info("PASS: Case 1 Successful Operation")

    # -----------------------------------------------------------------
    # CASE 2 — Transient Failure & Successful Retry
    # -----------------------------------------------------------------
    logger.info("\n--- CASE 2: Transient Failure & Successful Retry ---")
    attempt_c2 = 0

    def transient_then_success():
        nonlocal attempt_c2
        attempt_c2 += 1
        if attempt_c2 == 1:
            raise TransientError("Groq API timeout 503")
        return {"status": "SUCCESS", "message": "Recovered on retry"}

    res2_1, _ = execute_with_idempotency(
        customer_id=1,
        idempotency_key="key-c2-transient",
        operation_type="TEST_OP",
        request_params={"action": "test2"},
        action_fn=transient_then_success,
        db=db,
    )
    assert res2_1["status"] == "RETRYABLE_FAILED"
    assert "Groq API timeout 503" in res2_1["error"]

    rec2 = db.query(IdempotencyRecord).filter_by(idempotency_key="key-c2-transient").first()
    assert rec2.status == "RETRYABLE_FAILED"

    res2_2, _ = retry_idempotent_operation(
        customer_id=1,
        idempotency_key="key-c2-transient",
        action_fn=transient_then_success,
        db=db,
    )
    assert res2_2["status"] == "SUCCESS"
    rec2_updated = db.query(IdempotencyRecord).filter_by(idempotency_key="key-c2-transient").first()
    assert rec2_updated.status == "COMPLETED"
    assert rec2_updated.retry_count == 1, f"Expected retry_count=1, got {rec2_updated.retry_count}"
    assert attempt_c2 == 2, f"Expected total 2 attempts, got {attempt_c2}"
    logger.info("PASS: Case 2 Transient Failure & Successful Retry")

    # -----------------------------------------------------------------
    # CASE 3 — Permanent Failure (Authorization / Invalid Input)
    # -----------------------------------------------------------------
    logger.info("\n--- CASE 3: Permanent Failure ---")
    attempt_c3 = 0

    def permanent_fail_action():
        nonlocal attempt_c3
        attempt_c3 += 1
        return {"error": "Unauthorized: Customer 1 does not own Order #1002", "status": "FAILED"}

    res3_1, _ = execute_with_idempotency(
        customer_id=1,
        idempotency_key="key-c3-unauthorized",
        operation_type="TEST_OP",
        request_params={"order_id": 1002},
        action_fn=permanent_fail_action,
        db=db,
    )
    assert res3_1["status"] == "FAILED"
    rec3 = db.query(IdempotencyRecord).filter_by(idempotency_key="key-c3-unauthorized").first()
    assert rec3.status == "FAILED"

    # Retrying permanent failure must NOT re-execute action_fn
    res3_2, replayed3_2 = retry_idempotent_operation(
        customer_id=1,
        idempotency_key="key-c3-unauthorized",
        action_fn=permanent_fail_action,
        db=db,
    )
    assert replayed3_2, "Retry call should return stored response"
    assert res3_2["status"] == "FAILED"
    assert attempt_c3 == 1, "Permanent failure must NOT re-execute action_fn"
    logger.info("PASS: Case 3 Permanent Failure")

    # -----------------------------------------------------------------
    # CASE 4 — Retry Limit (MAX_RETRIES = 3 Exceeded)
    # -----------------------------------------------------------------
    logger.info("\n--- CASE 4: Retry Limit ---")
    attempt_c4 = 0

    def always_transient():
        nonlocal attempt_c4
        attempt_c4 += 1
        raise TransientError("Persistent network outage")

    execute_with_idempotency(
        customer_id=1,
        idempotency_key="key-c4-max-retries",
        operation_type="TEST_OP",
        request_params={"action": "test4"},
        action_fn=always_transient,
        db=db,
        max_retries=3,
    )

    for _ in range(3):
        retry_idempotent_operation(
            customer_id=1,
            idempotency_key="key-c4-max-retries",
            action_fn=always_transient,
            db=db,
        )

    rec4 = db.query(IdempotencyRecord).filter_by(idempotency_key="key-c4-max-retries").first()
    assert rec4.status == "FAILED", f"Expected final status FAILED, got {rec4.status}"
    assert rec4.retry_count >= 3, f"Expected retry_count >= 3, got {rec4.retry_count}"

    res4_stop, _ = retry_idempotent_operation(
        customer_id=1,
        idempotency_key="key-c4-max-retries",
        action_fn=always_transient,
        db=db,
    )
    assert res4_stop["status"] == "FAILED"
    logger.info("PASS: Case 4 Retry Limit")

    # -----------------------------------------------------------------
    # CASE 5 — Idempotent Retry Retains Same Action ID
    # -----------------------------------------------------------------
    logger.info("\n--- CASE 5: Idempotent Retry Retains Action ID ---")
    actions_created_c5 = []

    def action_with_db_record():
        act = Action(action_type="REFUND", reference_id=1003,
                     customer_id=1, amount=50.0, status="PENDING")
        db.add(act)
        db.commit()
        db.refresh(act)
        actions_created_c5.append(act.id)
        if len(actions_created_c5) == 1:
            return {"error": "Interrupted connection transient error", "action_id": act.id}
        act.status = "COMPLETED"
        db.commit()
        return {"status": "COMPLETED", "action_id": act.id}

    execute_with_idempotency(
        customer_id=1,
        idempotency_key="key-c5-action-reuse",
        operation_type="REFUND",
        request_params={"amount": 50},
        action_fn=action_with_db_record,
        db=db,
    )

    rec5 = db.query(IdempotencyRecord).filter_by(idempotency_key="key-c5-action-reuse").first()
    assert rec5.action_id == actions_created_c5[0], "Action ID must be linked to IdempotencyRecord"

    retry_idempotent_operation(
        customer_id=1,
        idempotency_key="key-c5-action-reuse",
        action_fn=action_with_db_record,
        db=db,
    )
    rec5_after = db.query(IdempotencyRecord).filter_by(idempotency_key="key-c5-action-reuse").first()
    assert rec5_after.action_id == actions_created_c5[0], \
        "Action ID must remain unchanged across retries"
    logger.info("PASS: Case 5 Action ID Preserved Across Retries")

    # -----------------------------------------------------------------
    # CASE 6 — High-Value Refund Awaiting Approval (PENDING_APPROVAL)
    # -----------------------------------------------------------------
    logger.info("\n--- CASE 6: High-Value Refund Awaiting Approval ---")
    approvals_created_c6 = 0

    def high_value_refund_action():
        nonlocal approvals_created_c6
        act = Action(action_type="REFUND", reference_id=1003,
                     customer_id=1, amount=5000.0, status="PENDING")
        db.add(act)
        db.commit()
        db.refresh(act)

        appr = Approval(action_id=act.id, status="PENDING",
                        reason="High value refund > $100 threshold")
        db.add(appr)
        db.commit()
        db.refresh(appr)
        approvals_created_c6 += 1

        return {
            "status": "PENDING_APPROVAL",
            "approval_status": "PENDING_APPROVAL",
            "approval_id": appr.id,
            "action_id": act.id,
        }

    res6, _ = execute_with_idempotency(
        customer_id=1,
        idempotency_key="key-c6-high-value",
        operation_type="REFUND",
        request_params={"amount": 5000},
        action_fn=high_value_refund_action,
        db=db,
    )
    assert res6["approval_status"] == "PENDING_APPROVAL"
    rec6 = db.query(IdempotencyRecord).filter_by(idempotency_key="key-c6-high-value").first()
    assert rec6.status == "PENDING_APPROVAL"
    assert approvals_created_c6 == 1, "Exactly one approval record must be created"

    res6_retry, _ = retry_idempotent_operation(
        customer_id=1,
        idempotency_key="key-c6-high-value",
        action_fn=high_value_refund_action,
        db=db,
    )
    assert res6_retry["approval_status"] == "PENDING_APPROVAL"
    assert approvals_created_c6 == 1, "Retrying pending approval must NOT create a 2nd approval"
    logger.info("PASS: Case 6 High-Value Refund Awaiting Approval")

    # -----------------------------------------------------------------
    # CASE 7 — Approved Refund Execution Recovery
    # -----------------------------------------------------------------
    logger.info("\n--- CASE 7: Approved Refund Execution Recovery ---")
    appr6 = db.query(Approval).filter_by(id=res6["approval_id"]).first()
    appr6.status = "APPROVED"
    act6 = db.query(Action).filter_by(id=res6["action_id"]).first()
    act6.status = "COMPLETED"
    db.commit()

    res7_retry, _ = retry_idempotent_operation(
        customer_id=1,
        idempotency_key="key-c6-high-value",
        db=db,
    )
    assert res7_retry["status"] == "COMPLETED"
    assert res7_retry["approval_status"] == "APPROVED"

    rec7 = db.query(IdempotencyRecord).filter_by(idempotency_key="key-c6-high-value").first()
    assert rec7.status == "COMPLETED"
    assert approvals_created_c6 == 1, "No extra approvals created on recovery"
    logger.info("PASS: Case 7 Approved Refund Execution Recovery")

    # -----------------------------------------------------------------
    # CASE 8 — Completed Operation Replay
    # -----------------------------------------------------------------
    logger.info("\n--- CASE 8: Completed Operation Replay ---")
    action_executed_c8 = 0

    def c8_action():
        nonlocal action_executed_c8
        action_executed_c8 += 1
        return {"status": "SUCCESS", "data": "original_payload"}

    execute_with_idempotency(
        customer_id=1,
        idempotency_key="key-c8-completed",
        operation_type="TEST_OP",
        request_params={"x": 1},
        action_fn=c8_action,
        db=db,
    )

    res8_replay, is_replayed8 = execute_with_idempotency(
        customer_id=1,
        idempotency_key="key-c8-completed",
        operation_type="TEST_OP",
        request_params={"x": 1},
        action_fn=c8_action,
        db=db,
    )
    assert is_replayed8, "Second call must return is_replayed=True"
    assert res8_replay["data"] == "original_payload"
    assert action_executed_c8 == 1, "Action function must NOT re-execute on completed replay"
    logger.info("PASS: Case 8 Completed Operation Replay")

    # -----------------------------------------------------------------
    # CASE 9 — Worker Transient Failure (Bounded Retry in Redis Queue)
    # -----------------------------------------------------------------
    logger.info("\n--- CASE 9: Worker Transient Failure ---")
    redis_client.delete(QUEUE_NAME)
    db.add(Ticket(id=901, customer_message="Transient test", status="PENDING"))
    db.commit()

    def mock_transient_classify(msg):
        raise TransientError("Groq 503 Service Unavailable")

    payload9 = json.dumps({"ticket_id": 901, "retry_count": 0})
    success = process_single_job(payload9, classify_fn=mock_transient_classify)
    assert success, "Job should return True indicating successful handling/re-enqueue"

    queue_len = redis_client.llen(QUEUE_NAME)
    assert queue_len == 1, f"Expected 1 job in Redis queue, got {queue_len}"

    reenqueued_json = redis_client.lpop(QUEUE_NAME)
    reenqueued_data = json.loads(reenqueued_json)
    assert reenqueued_data["ticket_id"] == 901
    assert reenqueued_data["retry_count"] == 1, \
        f"Expected retry_count=1, got {reenqueued_data['retry_count']}"
    logger.info("PASS: Case 9 Worker Transient Failure")

    # -----------------------------------------------------------------
    # CASE 10 — Worker Permanent Failure (Job Discarded)
    # -----------------------------------------------------------------
    logger.info("\n--- CASE 10: Worker Permanent Failure ---")
    ticket10 = Ticket(id=1001, customer_message="Permanent test", status="PENDING")
    db.add(ticket10)
    db.commit()

    def mock_permanent_classify(msg):
        raise PermanentError("Malformed customer prompt")

    payload10 = json.dumps({"ticket_id": 1001, "retry_count": 0})
    process_single_job(payload10, classify_fn=mock_permanent_classify)

    db.refresh(ticket10)
    assert ticket10.status == "CLASSIFICATION_FAILED", \
        f"Expected CLASSIFICATION_FAILED, got {ticket10.status}"
    queue_len10 = redis_client.llen(QUEUE_NAME)
    assert queue_len10 == 0, "Permanent failure must NOT re-enqueue into queue"
    logger.info("PASS: Case 10 Worker Permanent Failure")

    # -----------------------------------------------------------------
    db.close()
    logger.info("\n=========================================================")
    logger.info("ALL 10 STAGE 7D FAILURE RECOVERY & RETRY TESTS PASSED!")
    logger.info("=========================================================")


if __name__ == "__main__":
    run_tests()
