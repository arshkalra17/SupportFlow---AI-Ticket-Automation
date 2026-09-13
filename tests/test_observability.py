"""
Stage 10 — Observability instrumentation tests.

Verifies that telemetry decorators and instrumentation:
- Do not break existing business logic
- Fail-open (errors in telemetry do not propagate to business code)
- Preserve all return values and exceptions
- Do not modify function signatures
"""

import pytest
from unittest.mock import patch, MagicMock
from opentelemetry import trace

from app.observability import traced_span, safe_set_attribute, hash_sensitive
from app.idempotency import compute_request_hash, execute_with_idempotency
from app.queue import enqueue_ticket_processing
from app.worker import process_single_job


# ══════════════════════════════════════════════════════════════════════
# Utility function tests
# ══════════════════════════════════════════════════════════════════════

class TestObservabilityUtilities:
    def test_hash_sensitive_returns_truncated_hash(self):
        result = hash_sensitive("secret-idempotency-key-12345")
        assert len(result) == 16  # truncated to 16 chars
        assert result != "secret-idempotency-key-12345"
        # Verify it's a valid hex string
        assert all(c in '0123456789abcdef' for c in result)

    def test_hash_sensitive_handles_none(self):
        result = hash_sensitive(None)
        assert result == ""

    def test_hash_sensitive_handles_empty_string(self):
        result = hash_sensitive("")
        assert result == ""

    def test_safe_set_attribute_does_not_raise_on_error(self):
        """safe_set_attribute must fail silently when span operations fail."""
        mock_span = MagicMock()
        mock_span.set_attribute.side_effect = Exception("Telemetry backend down")

        # Must not raise
        safe_set_attribute(mock_span, "test.key", "test_value")
        assert mock_span.set_attribute.called


# ══════════════════════════════════════════════════════════════════════
# Decorator fail-open behavior
# ══════════════════════════════════════════════════════════════════════

class TestTracedSpanFailOpen:
    def test_traced_span_preserves_return_value(self):
        @traced_span("test.function")
        def my_func(x, y):
            return x + y

        result = my_func(3, 7)
        assert result == 10

    def test_traced_span_preserves_exceptions(self):
        @traced_span("test.error_function")
        def raises_error():
            raise ValueError("business exception")

        with pytest.raises(ValueError, match="business exception"):
            raises_error()

    def test_traced_span_does_not_break_on_telemetry_failure(self):
        """If span creation or ending fails, business logic must still execute."""
        @traced_span("test.telemetry_broken")
        def business_logic():
            return "success"

        with patch("opentelemetry.trace.get_tracer") as mock_tracer:
            mock_tracer.return_value.start_as_current_span.side_effect = Exception("OTel backend down")
            result = business_logic()
            assert result == "success"


# ══════════════════════════════════════════════════════════════════════
# Instrumented function integration tests
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestInstrumentedIdempotency:
    def test_compute_request_hash_still_deterministic(self):
        """Telemetry must not affect hash computation."""
        h1 = compute_request_hash({"a": 1, "b": 2})
        h2 = compute_request_hash({"b": 2, "a": 1})
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex digest

    def test_execute_with_idempotency_preserves_behavior(self, db, customer):
        """Instrumentation must not alter idempotency logic."""
        calls = []

        def action():
            calls.append(1)
            return {"status": "SUCCESS", "value": 123}

        result, replayed = execute_with_idempotency(
            customer_id=customer.id,
            idempotency_key="obs-test-key",
            operation_type="TEST",
            request_params={"x": 1},
            action_fn=action,
            db=db,
        )
        assert replayed is False
        assert result["value"] == 123
        assert len(calls) == 1

        # Second call must replay
        result2, replayed2 = execute_with_idempotency(
            customer_id=customer.id,
            idempotency_key="obs-test-key",
            operation_type="TEST",
            request_params={"x": 1},
            action_fn=action,
            db=db,
        )
        assert replayed2 is True
        assert result2["value"] == 123
        assert len(calls) == 1  # action not re-executed


@pytest.mark.integration
class TestInstrumentedQueue:
    def test_enqueue_ticket_processing_preserves_behavior(self, fake_redis):
        """Instrumentation must not alter queue enqueue logic."""
        from app.queue import QUEUE_NAME
        payload = enqueue_ticket_processing(999)
        assert '"ticket_id": 999' in payload
        assert fake_redis.llen(QUEUE_NAME) == 1


@pytest.mark.integration
class TestInstrumentedWorker:
    def test_process_single_job_preserves_behavior(self, db, fake_redis, monkeypatch):
        """Instrumentation must not alter worker job processing."""
        from app.models import Ticket
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import NullPool
        import os
        import json

        # Seed ticket
        ticket = Ticket(id=998, customer_message="test message", status="PENDING")
        db.add(ticket)
        db.commit()

        # Monkeypatch worker SessionLocal
        engine = create_engine(os.environ["DATABASE_URL"], poolclass=NullPool)
        TestSession = sessionmaker(bind=engine)
        monkeypatch.setattr("app.worker.SessionLocal", TestSession)

        def mock_classify(msg):
            return {"category": "BILLING", "priority": "HIGH"}

        payload = json.dumps({"ticket_id": 998})
        result = process_single_job(payload, classify_fn=mock_classify)

        assert result is True
        updated = db.query(Ticket).filter_by(id=998).first()
        assert updated.status == "PROCESSED"
        assert updated.category == "BILLING"
        assert updated.priority == "HIGH"


# ══════════════════════════════════════════════════════════════════════
# Span attribute validation
# ══════════════════════════════════════════════════════════════════════

class TestSpanAttributes:
    def test_idempotency_execution_does_not_crash_with_telemetry(self, db, customer):
        """Verify that telemetry integration doesn't break idempotency execution."""
        def action():
            return {"status": "SUCCESS"}

        # This should complete without raising exceptions
        result, replayed = execute_with_idempotency(
            customer_id=customer.id,
            idempotency_key="attr-test-key",
            operation_type="TEST",
            request_params={"x": 1},
            action_fn=action,
            db=db,
        )

        assert result["status"] == "SUCCESS"
        assert replayed is False
