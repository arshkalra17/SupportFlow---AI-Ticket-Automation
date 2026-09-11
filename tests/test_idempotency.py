"""
Stage 8 — Idempotency and failure recovery tests (deterministic, no Groq).

Covers:
- First request executes action
- Exact replay returns stored result without re-executing
- Same key + different payload → HTTP 409
- Tenant isolation (same key, different customers)
- Concurrent identical requests produce exactly one action
- Same Action ID preserved across retries
- Completed operation replay
- PENDING_APPROVAL replay (not re-triggered as error)
- Transient failure → RETRYABLE_FAILED
- Transient failure → retry → success
- Permanent failure → FAILED, never retried
- Retry limit (MAX_RETRIES=3) → FAILED
- Worker transient failure → re-enqueue
- Worker permanent failure → CLASSIFICATION_FAILED, no re-enqueue
"""

import json
import threading
import pytest

from app.idempotency import (
    execute_with_idempotency,
    retry_idempotent_operation,
    TransientError,
    PermanentError,
    compute_request_hash,
)
from app.models import IdempotencyRecord, Action, Approval, Ticket
from app.worker import process_single_job
from app.queue import QUEUE_NAME
from fastapi import HTTPException


# ══════════════════════════════════════════════════════════════════════
# compute_request_hash — determinism
# ══════════════════════════════════════════════════════════════════════

class TestRequestHash:
    def test_same_params_same_hash(self):
        h1 = compute_request_hash({"a": 1, "b": 2})
        h2 = compute_request_hash({"a": 1, "b": 2})
        assert h1 == h2

    def test_key_order_does_not_matter(self):
        h1 = compute_request_hash({"a": 1, "b": 2})
        h2 = compute_request_hash({"b": 2, "a": 1})
        assert h1 == h2

    def test_different_params_different_hash(self):
        h1 = compute_request_hash({"a": 1})
        h2 = compute_request_hash({"a": 2})
        assert h1 != h2


# ══════════════════════════════════════════════════════════════════════
# execute_with_idempotency — core behaviors
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestIdempotencyCore:
    def test_first_call_executes_action(self, db, customer):
        calls = []

        def action():
            calls.append(1)
            return {"status": "SUCCESS", "value": 42}

        result, replayed = execute_with_idempotency(
            customer_id=customer.id,
            idempotency_key="key-first-call",
            operation_type="TEST",
            request_params={"x": 1},
            action_fn=action,
            db=db,
        )
        assert replayed is False
        assert result["status"] == "SUCCESS"
        assert len(calls) == 1

    def test_second_call_replays_without_re_executing(self, db, customer):
        calls = []

        def action():
            calls.append(1)
            return {"status": "SUCCESS", "value": 99}

        execute_with_idempotency(
            customer_id=customer.id,
            idempotency_key="key-replay",
            operation_type="TEST",
            request_params={"x": 1},
            action_fn=action,
            db=db,
        )
        result, replayed = execute_with_idempotency(
            customer_id=customer.id,
            idempotency_key="key-replay",
            operation_type="TEST",
            request_params={"x": 1},
            action_fn=action,
            db=db,
        )
        assert replayed is True
        assert result["value"] == 99
        # Action must only have run once
        assert len(calls) == 1

    def test_same_key_different_payload_raises_409(self, db, customer):
        def action():
            return {"status": "SUCCESS"}

        execute_with_idempotency(
            customer_id=customer.id,
            idempotency_key="key-conflict",
            operation_type="TEST",
            request_params={"x": 1},
            action_fn=action,
            db=db,
        )
        with pytest.raises(HTTPException) as exc_info:
            execute_with_idempotency(
                customer_id=customer.id,
                idempotency_key="key-conflict",
                operation_type="TEST",
                request_params={"x": 99},  # different payload
                action_fn=action,
                db=db,
            )
        assert exc_info.value.status_code == 409

    def test_tenant_isolation_same_key_different_customers(self, db, customer, customer2):
        """Same idempotency key must be scoped per customer."""
        results = []

        def action():
            results.append(len(results))
            return {"status": "SUCCESS", "idx": len(results)}

        execute_with_idempotency(
            customer_id=customer.id,
            idempotency_key="shared-key",
            operation_type="TEST",
            request_params={"x": 1},
            action_fn=action,
            db=db,
        )
        execute_with_idempotency(
            customer_id=customer2.id,
            idempotency_key="shared-key",
            operation_type="TEST",
            request_params={"x": 1},
            action_fn=action,
            db=db,
        )
        # Both customers executed the action independently
        assert len(results) == 2

    def test_no_idempotency_key_always_executes(self, db, customer):
        calls = []

        def action():
            calls.append(1)
            return {"status": "SUCCESS"}

        execute_with_idempotency(
            customer_id=customer.id,
            idempotency_key=None,  # no key
            operation_type="TEST",
            request_params={"x": 1},
            action_fn=action,
            db=db,
        )
        execute_with_idempotency(
            customer_id=customer.id,
            idempotency_key=None,
            operation_type="TEST",
            request_params={"x": 1},
            action_fn=action,
            db=db,
        )
        assert len(calls) == 2

    def test_record_written_to_database(self, db, customer):
        def action():
            return {"status": "SUCCESS", "thing": "abc"}

        execute_with_idempotency(
            customer_id=customer.id,
            idempotency_key="key-db-write",
            operation_type="SUPPORT_PROCESS",
            request_params={"message": "hello"},
            action_fn=action,
            db=db,
        )
        record = db.query(IdempotencyRecord).filter_by(
            idempotency_key="key-db-write",
            customer_id=customer.id,
        ).first()
        assert record is not None
        assert record.status == "COMPLETED"
        assert record.response_data["thing"] == "abc"


# ══════════════════════════════════════════════════════════════════════
# Transient / permanent failure classification
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestFailureClassification:
    def test_transient_exception_sets_retryable_failed(self, db, customer):
        def action():
            raise TransientError("Groq 503 timeout")

        result, _ = execute_with_idempotency(
            customer_id=customer.id,
            idempotency_key="key-transient",
            operation_type="TEST",
            request_params={"x": 1},
            action_fn=action,
            db=db,
        )
        assert result["status"] == "RETRYABLE_FAILED"
        record = db.query(IdempotencyRecord).filter_by(
            idempotency_key="key-transient"
        ).first()
        assert record.status == "RETRYABLE_FAILED"

    def test_transient_failure_then_successful_retry(self, db, customer):
        attempts = []

        def action():
            attempts.append(1)
            if len(attempts) == 1:
                raise TransientError("first attempt fails")
            return {"status": "SUCCESS", "recovered": True}

        execute_with_idempotency(
            customer_id=customer.id,
            idempotency_key="key-recover",
            operation_type="TEST",
            request_params={"x": 1},
            action_fn=action,
            db=db,
        )
        result, _ = retry_idempotent_operation(
            customer_id=customer.id,
            idempotency_key="key-recover",
            action_fn=action,
            db=db,
        )
        assert result["status"] == "SUCCESS"
        assert result["recovered"] is True
        record = db.query(IdempotencyRecord).filter_by(
            idempotency_key="key-recover"
        ).first()
        assert record.status == "COMPLETED"
        assert record.retry_count == 1

    def test_permanent_failure_not_retried(self, db, customer):
        calls = []

        def action():
            calls.append(1)
            return {
                "error": "Unauthorized: Customer does not own this order",
                "status": "FAILED",
            }

        execute_with_idempotency(
            customer_id=customer.id,
            idempotency_key="key-perm-fail",
            operation_type="TEST",
            request_params={"x": 1},
            action_fn=action,
            db=db,
        )
        # Retry of permanent failure must NOT re-execute
        result, replayed = retry_idempotent_operation(
            customer_id=customer.id,
            idempotency_key="key-perm-fail",
            action_fn=action,
            db=db,
        )
        assert replayed is True
        assert result["status"] == "FAILED"
        assert len(calls) == 1  # executed exactly once

    def test_max_retries_exceeded_transitions_to_failed(self, db, customer):
        def always_fails():
            raise TransientError("persistent outage")

        execute_with_idempotency(
            customer_id=customer.id,
            idempotency_key="key-max-retries",
            operation_type="TEST",
            request_params={"x": 1},
            action_fn=always_fails,
            db=db,
            max_retries=3,
        )
        for _ in range(3):
            retry_idempotent_operation(
                customer_id=customer.id,
                idempotency_key="key-max-retries",
                action_fn=always_fails,
                db=db,
            )
        record = db.query(IdempotencyRecord).filter_by(
            idempotency_key="key-max-retries"
        ).first()
        assert record.status == "FAILED"
        assert record.retry_count >= 3

        # Further retry call must return FAILED without calling action
        final_calls = []

        def not_called():
            final_calls.append(1)
            return {"status": "SUCCESS"}

        result, _ = retry_idempotent_operation(
            customer_id=customer.id,
            idempotency_key="key-max-retries",
            action_fn=not_called,
            db=db,
        )
        assert result["status"] == "FAILED"
        assert len(final_calls) == 0

    def test_pending_approval_not_treated_as_error(self, db, customer, orders):
        """
        A result carrying approval_status=PENDING_APPROVAL must be stored
        as PENDING_APPROVAL on the IdempotencyRecord, never as FAILED or
        RETRYABLE_FAILED.  Retrying the same key must replay the pending
        state without re-executing the action.

        We create a real Action + Approval row so the FK on
        idempotency_records.action_id is satisfied.
        """
        from app.models import Action, Approval

        # Create real DB records so FK constraints are satisfied
        action_row = Action(
            action_type="REFUND",
            reference_id=1006,
            customer_id=customer.id,
            amount=5000.0,
            status="PENDING",
        )
        db.add(action_row)
        db.commit()
        db.refresh(action_row)

        approval_row = Approval(action_id=action_row.id, status="PENDING")
        db.add(approval_row)
        db.commit()
        db.refresh(approval_row)

        def action():
            return {
                "status": "PENDING_APPROVAL",
                "approval_status": "PENDING_APPROVAL",
                "approval_id": approval_row.id,
                "action_id": action_row.id,
            }

        result, _ = execute_with_idempotency(
            customer_id=customer.id,
            idempotency_key="key-pending-appr",
            operation_type="REFUND",
            request_params={"amount": 5000},
            action_fn=action,
            db=db,
        )
        assert result["approval_status"] == "PENDING_APPROVAL"
        record = db.query(IdempotencyRecord).filter_by(
            idempotency_key="key-pending-appr"
        ).first()
        # PENDING_APPROVAL is a legitimate workflow state — never FAILED
        assert record.status == "PENDING_APPROVAL"

        # Retrying a pending approval must replay it without re-executing the action
        calls = []

        def should_not_run():
            calls.append(1)
            return {"status": "SUCCESS"}

        retry_result, replayed = retry_idempotent_operation(
            customer_id=customer.id,
            idempotency_key="key-pending-appr",
            action_fn=should_not_run,
            db=db,
        )
        assert retry_result["approval_status"] == "PENDING_APPROVAL"
        assert len(calls) == 0


# ══════════════════════════════════════════════════════════════════════
# Action ID preservation across retries
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestActionIdPreservation:
    def test_same_action_id_across_retry(self, db, customer, orders):
        """Retry must reuse the same Action record, not create a new one."""
        created_action_ids = []

        def action():
            act = Action(
                action_type="REFUND",
                reference_id=1006,
                customer_id=customer.id,
                amount=50.0,
                status="PENDING",
            )
            db.add(act)
            db.commit()
            db.refresh(act)
            created_action_ids.append(act.id)
            # First attempt: simulate partial failure
            if len(created_action_ids) == 1:
                return {
                    "error": "transient network error",
                    "action_id": act.id,
                }
            act.status = "COMPLETED"
            db.commit()
            return {"status": "COMPLETED", "action_id": act.id}

        execute_with_idempotency(
            customer_id=customer.id,
            idempotency_key="key-action-id",
            operation_type="REFUND",
            request_params={"amount": 50},
            action_fn=action,
            db=db,
        )
        record = db.query(IdempotencyRecord).filter_by(
            idempotency_key="key-action-id"
        ).first()
        first_action_id = record.action_id
        assert first_action_id == created_action_ids[0]

        retry_idempotent_operation(
            customer_id=customer.id,
            idempotency_key="key-action-id",
            action_fn=action,
            db=db,
        )
        record_after = db.query(IdempotencyRecord).filter_by(
            idempotency_key="key-action-id"
        ).first()
        assert record_after.action_id == first_action_id


# ══════════════════════════════════════════════════════════════════════
# Concurrency: exactly one action created
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.integration
@pytest.mark.slow
class TestConcurrentIdempotency:
    def test_concurrent_requests_produce_one_action(self, db, customer):
        """
        10 concurrent threads firing the same idempotency key must result
        in exactly one business-action execution.

        Each thread opens its own DB session (mirroring production behaviour
        where every HTTP request gets its own session).  The PostgreSQL
        UNIQUE constraint on (customer_id, idempotency_key) is the
        concurrency safety mechanism; this test verifies it holds under load.

        We use NullPool so each thread gets a fresh, independent connection
        with no risk of SQLAlchemy cross-thread session sharing.
        """
        import concurrent.futures
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import NullPool
        import os

        test_url = os.environ["DATABASE_URL"]
        # NullPool: each session gets its own psycopg2 connection,
        # no pool reuse across threads.
        thread_engine = create_engine(test_url, poolclass=NullPool)
        ThreadSession = sessionmaker(bind=thread_engine)

        # customer was seeded by the fixture and committed — visible to all threads.
        customer_id = customer.id

        action_count = {"n": 0}
        lock = threading.Lock()

        def threaded_task():
            sess = ThreadSession()
            try:
                def action():
                    with lock:
                        action_count["n"] += 1
                    return {"status": "SUCCESS", "val": 1}

                result, _ = execute_with_idempotency(
                    customer_id=customer_id,
                    idempotency_key="key-concurrent",
                    operation_type="TEST",
                    request_params={"x": 1},
                    action_fn=action,
                    db=sess,
                )
                return result
            except HTTPException:
                return None
            finally:
                sess.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(threaded_task) for _ in range(10)]
            concurrent.futures.wait(futures)

        # The UNIQUE constraint must have ensured exactly one action execution
        assert action_count["n"] == 1, (
            f"Expected exactly 1 action execution across 10 concurrent requests, "
            f"got {action_count['n']}"
        )


# ══════════════════════════════════════════════════════════════════════
# Worker retry behavior
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestWorkerRetry:
    """
    Worker retry tests.

    Isolation: monkeypatch app.worker.SessionLocal with a factory that
    returns a dedicated NullPool session pointing at the test database.
    This avoids passing the fixture's session directly into the worker,
    which would be closed by the worker's own `finally: db.close()` call.

    After process_single_job() returns, we re-query the ticket through
    the fixture session (which sees the worker's committed writes via
    READ COMMITTED isolation).
    """

    def _worker_session_factory(self):
        """Returns a sessionmaker backed by a NullPool test-DB engine."""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import NullPool
        import os
        engine = create_engine(os.environ["DATABASE_URL"], poolclass=NullPool)
        return sessionmaker(bind=engine)

    def _seed_ticket(self, db, ticket_id: int, message: str = "test message"):
        ticket = Ticket(id=ticket_id, customer_message=message, status="PENDING")
        db.add(ticket)
        db.commit()
        return ticket

    def test_transient_failure_reenqueues_job(self, db, fake_redis, monkeypatch):
        """Transient classify error must re-enqueue with incremented retry_count."""
        self._seed_ticket(db, 901)
        monkeypatch.setattr("app.worker.SessionLocal", self._worker_session_factory())

        def transient_classify(msg):
            raise TransientError("Groq 503")

        payload = json.dumps({"ticket_id": 901, "retry_count": 0})
        result = process_single_job(payload, classify_fn=transient_classify)

        assert result is True
        queue_len = fake_redis.llen(QUEUE_NAME)
        assert queue_len == 1
        requeued = json.loads(fake_redis.lpop(QUEUE_NAME))
        assert requeued["ticket_id"] == 901
        assert requeued["retry_count"] == 1

    def test_permanent_failure_sets_classification_failed(self, db, fake_redis, monkeypatch):
        """Permanent classify error must mark ticket CLASSIFICATION_FAILED, no re-enqueue."""
        self._seed_ticket(db, 902)
        monkeypatch.setattr("app.worker.SessionLocal", self._worker_session_factory())

        def permanent_classify(msg):
            raise PermanentError("Malformed prompt")

        payload = json.dumps({"ticket_id": 902, "retry_count": 0})
        process_single_job(payload, classify_fn=permanent_classify)

        # Re-query via fixture session — worker committed via its own session
        updated = db.query(Ticket).filter_by(id=902).first()
        assert updated.status == "CLASSIFICATION_FAILED"
        assert fake_redis.llen(QUEUE_NAME) == 0

    def test_worker_does_not_retry_beyond_max(self, db, fake_redis, monkeypatch):
        """At retry_count == MAX_JOB_RETRIES a transient error must NOT re-enqueue."""
        from app.worker import MAX_JOB_RETRIES

        self._seed_ticket(db, 903)
        monkeypatch.setattr("app.worker.SessionLocal", self._worker_session_factory())

        def transient_classify(msg):
            raise TransientError("perpetual outage")

        payload = json.dumps({"ticket_id": 903, "retry_count": MAX_JOB_RETRIES})
        process_single_job(payload, classify_fn=transient_classify)

        assert fake_redis.llen(QUEUE_NAME) == 0
        updated = db.query(Ticket).filter_by(id=903).first()
        assert updated.status == "CLASSIFICATION_FAILED"
