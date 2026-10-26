"""
Failure injection: what the system does when the model, the queue or the
database fails part-way through, not only at the start.

The model is mocked. The interesting cases are the ones where a failure lands
*after* a side effect, because that is where a customer can be told "something
went wrong" about a refund that was in fact issued, and then ask again.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
import redis
from sqlalchemy.exc import OperationalError

from app.conversation import get_or_create_conversation
from app.llm_client import LLMResult, LLMUnavailableError
from app.models import Action, Order, ReplacementRequest, Ticket

pytestmark = pytest.mark.integration

APOLOGY = "I apologize, but I encountered an issue processing your request."


def _reply(tool_name=None, args=None, content=None):
    message = MagicMock()
    message.content = content
    if tool_name:
        call = MagicMock()
        call.id = "call_1"
        call.function.name = tool_name
        call.function.arguments = json.dumps(args)
        message.tool_calls = [call]
    else:
        message.tool_calls = None
    response = MagicMock()
    response.choices = [MagicMock(message=message)]
    return LLMResult(response=response, provider="groq", model="test")


def _run(customer, db, message, replies, category="Order Issue"):
    """Run the graph with the classifier fixed and the model returning `replies` in order
    (an Exception in the list is raised instead of returned)."""
    from app.graph import run_supportflow

    conversation = get_or_create_conversation(customer.id, db)
    with patch("app.graph.classify_ticket", return_value={
        "category": category, "priority": "MEDIUM", "sentiment": "NEUTRAL",
    }), patch("app.graph.chat_completion", side_effect=replies) as model:
        state = run_supportflow(
            customer_message=message,
            authenticated_customer_id=customer.id,
            conversation_id=conversation.id,
        )
    return state, model


# ── the model fails after an action already went through ─────────────

def test_model_failure_after_a_cancellation_still_reports_the_cancellation(db, customer, orders):
    state, _ = _run(customer, db, "Please cancel order 1003",
                    [_reply("cancel_order", {"order_id": 1003}), RuntimeError("provider down")])

    db.expire_all()
    assert db.get(Order, 1003).status == "CANCELLED"
    assert "cancelled" in state["final_response"].lower()
    assert "could duplicate" in state["final_response"] or "duplicate it" in state["final_response"]
    assert APOLOGY not in state["final_response"]
    assert state["error"]  # the failure is still recorded


def test_model_failure_after_a_refund_reports_the_refund_not_a_bare_apology(db, customer, orders):
    state, _ = _run(customer, db, "Please refund $50 for order 1002",
                    [_reply("issue_refund", {"order_id": 1002, "amount": 50.0}), RuntimeError("provider down")])

    db.expire_all()
    assert db.query(Action).filter_by(action_type="REFUND", reference_id=1002, status="COMPLETED").count() == 1
    assert "$50.00" in state["final_response"] and "issued" in state["final_response"]


def test_model_failure_after_a_replacement_reports_the_request_id(db, customer, orders):
    state, _ = _run(customer, db, "Please send a replacement for order 1002",
                    [_reply("create_replacement_request", {"order_id": 1002}), RuntimeError("provider down")])

    db.expire_all()
    created = db.query(ReplacementRequest).filter_by(order_id=1002).one()
    assert f"#{created.id}" in state["final_response"]


# ── the model fails before anything happened ─────────────────────────

def test_model_failure_before_any_action_is_a_plain_apology_and_changes_nothing(db, customer, orders):
    state, _ = _run(customer, db, "Please cancel order 1003", [RuntimeError("provider down")])

    db.expire_all()
    assert db.get(Order, 1003).status == "PROCESSING"
    assert state["final_response"].startswith(APOLOGY)
    assert state["error"]


def test_a_failed_classifier_is_a_plain_apology_and_runs_no_tools(db, customer, orders):
    from app.graph import run_supportflow

    conversation = get_or_create_conversation(customer.id, db)
    with patch("app.graph.classify_ticket", side_effect=LLMUnavailableError("all providers down")), \
            patch("app.graph.chat_completion") as model:
        state = run_supportflow(
            customer_message="Please cancel order 1003",
            authenticated_customer_id=customer.id,
            conversation_id=conversation.id,
        )

    model.assert_not_called()
    assert state["final_response"].startswith(APOLOGY)
    assert not state.get("tool_calls")
    db.expire_all()
    assert db.get(Order, 1003).status == "PROCESSING"


def test_a_guarded_request_is_not_reported_as_done_when_the_model_then_fails(db, customer, orders):
    """The guard blocks a *question*; if the model then fails, nothing was done and nothing is claimed."""
    state, _ = _run(customer, db, "Is order 1003 eligible to be cancelled?",
                    [_reply("cancel_order", {"order_id": 1003}), RuntimeError("provider down")])

    db.expire_all()
    assert db.get(Order, 1003).status == "PROCESSING"
    assert "cancelled" not in state["final_response"].lower()


# ── approvals do not depend on a second model call ───────────────────

def test_a_refund_needing_approval_is_queued_without_a_second_model_call(db, customer, orders):
    """The 'submitted for approval' reply is built by the backend, so the model dying
    right after the refund request cannot lose or garble the approval."""
    state, model = _run(customer, db, "Refund $1100 for order 1006",
                        [_reply("issue_refund", {"order_id": 1006, "amount": 1100.0}), RuntimeError("provider down")],
                        category="Refund Request")

    assert state["approval_status"] == "PENDING_APPROVAL"
    assert "submitted for human approval" in state["final_response"]
    assert model.call_count == 1


# ── over HTTP ────────────────────────────────────────────────────────

def test_api_answers_200_with_a_safe_message_when_every_provider_is_down(client, customer, orders, auth_headers):
    with patch("app.graph.classify_ticket", side_effect=LLMUnavailableError("all providers down")):
        resp = client.post("/support/process", json={"message": "where is my order 1001"}, headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["final_response"].startswith(APOLOGY)
    assert body["error"]


def _post(client, headers, message, replies):
    with patch("app.graph.classify_ticket", return_value={"category": "Order Issue", "priority": "MEDIUM", "sentiment": "NEUTRAL"}), \
            patch("app.graph.chat_completion", side_effect=replies) as model:
        return client.post("/support/process", json={"message": message}, headers=headers), model


def _key_status(db, key):
    from app.models import IdempotencyRecord
    db.expire_all()
    return db.query(IdempotencyRecord).filter_by(idempotency_key=key).one().status


def test_retry_after_a_failure_that_followed_a_refund_replays_instead_of_refunding_twice(client, db, customer, orders, auth_headers):
    """The case idempotency exists for: the refund went through, the model then died, the client retries."""
    headers = {**auth_headers, "Idempotency-Key": "refund-then-model-failure"}
    message = "Please refund $50 for order 1002"

    first, _ = _post(client, headers, message,
                     [_reply("issue_refund", {"order_id": 1002, "amount": 50.0}), RuntimeError("down")])
    second, model = _post(client, headers, message,
                          [_reply("issue_refund", {"order_id": 1002, "amount": 50.0}), _reply(content="Done")])

    assert "$50.00" in first.json()["final_response"]
    assert second.json()["idempotency_replayed"] is True
    assert second.json()["final_response"] == first.json()["final_response"]
    model.assert_not_called()
    assert _key_status(db, "refund-then-model-failure") == "COMPLETED"
    assert db.query(Action).filter_by(action_type="REFUND", reference_id=1002, status="COMPLETED").count() == 1


def test_retry_after_a_failure_that_followed_a_cancellation_replays(client, db, customer, orders, auth_headers):
    headers = {**auth_headers, "Idempotency-Key": "cancel-then-model-failure"}

    first, _ = _post(client, headers, "Please cancel order 1003",
                     [_reply("cancel_order", {"order_id": 1003}), RuntimeError("down")])
    second, model = _post(client, headers, "Please cancel order 1003", [_reply(content="should not be called")])

    assert "cancelled" in first.json()["final_response"].lower()
    assert second.json()["idempotency_replayed"] is True
    model.assert_not_called()
    assert _key_status(db, "cancel-then-model-failure") == "COMPLETED"


def test_a_failure_before_any_action_stays_retryable_with_the_same_key(client, db, customer, orders, auth_headers):
    """The other side: nothing happened, so the retry must actually run."""
    headers = {**auth_headers, "Idempotency-Key": "model-failure-first"}

    first, _ = _post(client, headers, "Please cancel order 1003", [RuntimeError("down")])
    assert _key_status(db, "model-failure-first") == "RETRYABLE_FAILED"

    second, _ = _post(client, headers, "Please cancel order 1003",
                      [_reply("cancel_order", {"order_id": 1003}), _reply(content="Order 1003 is cancelled.")])

    db.expire_all()
    assert db.get(Order, 1003).status == "CANCELLED"  # the retry really ran
    assert _key_status(db, "model-failure-first") == "COMPLETED"


def test_ticket_is_marked_when_the_queue_is_down(client, db):
    with patch("app.main.enqueue_ticket_processing", side_effect=redis.ConnectionError("redis down")):
        resp = client.post("/tickets", json={"message": "My order is late."})

    assert resp.status_code == 500
    db.expire_all()
    assert db.query(Ticket).one().status == "QUEUE_FAILED"


def test_support_endpoint_keeps_working_when_the_rate_limiter_store_is_down(client, customer, orders, auth_headers, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "1")
    broken = MagicMock()
    broken.pipeline.side_effect = redis.ConnectionError("redis down")
    broken.mget.side_effect = redis.ConnectionError("redis down")
    monkeypatch.setattr("app.queue.redis_client", broken)

    state = {"final_response": "ok", "classification": {"category": "General Inquiry"},
             "tool_calls": [], "approval_status": None, "retrieved_documents": [], "error": None}
    with patch("app.main.run_supportflow", return_value=state):
        resp = client.post("/support/process", json={"message": "hi"}, headers=auth_headers)

    assert resp.status_code == 200


# ── the worker ───────────────────────────────────────────────────────

class _BrokenSession:
    def query(self, *args, **kwargs):
        raise OperationalError("SELECT", {}, Exception("connection lost"))

    def rollback(self):
        pass

    def close(self):
        pass


def _queued(redis_client, name):
    return [json.loads(item) for item in redis_client.lrange(name, 0, -1)]


def test_worker_requeues_a_job_when_the_database_fails(fake_redis):
    from app import worker

    with patch.object(worker, "SessionLocal", _BrokenSession):
        assert worker.process_single_job(json.dumps({"ticket_id": 7})) is False

    assert _queued(fake_redis, worker.QUEUE_NAME) == [{"ticket_id": 7, "retry_count": 1}]


def test_worker_parks_a_job_that_keeps_failing_instead_of_dropping_it(fake_redis):
    from app import worker

    with patch.object(worker, "SessionLocal", _BrokenSession):
        worker.process_single_job(json.dumps({"ticket_id": 7, "retry_count": worker.MAX_JOB_RETRIES}))

    assert _queued(fake_redis, worker.QUEUE_NAME) == []
    assert _queued(fake_redis, worker.DEAD_LETTER_QUEUE) == [{"ticket_id": 7, "retry_count": worker.MAX_JOB_RETRIES + 1}]


def test_worker_backs_off_between_transient_retries(monkeypatch):
    from app import worker

    monkeypatch.setenv("WORKER_RETRY_BACKOFF_SECONDS", "2")
    assert [worker._retry_delay(n) for n in range(6)] == [2, 4, 8, 16, 30, 30]
    monkeypatch.setenv("WORKER_RETRY_BACKOFF_SECONDS", "0")
    assert worker._retry_delay(3) == 0
