"""
Approval interrupts: a refund that needs approval pauses the LangGraph run
(interrupt + Postgres checkpoint), and the admin's approve/reject resumes
that same run, which posts the outcome to the customer's conversation.

The LLM is mocked: the classifier says "Refund Request" and the tool loop
asks for a $1,100 refund on order 1006, which needs human approval.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from app.auth import create_access_token, hash_password
from app.checkpoint import close_checkpointer
from app.conversation import get_or_create_conversation
from app.llm_client import LLMResult
from app.models import Action, Approval, Customer, Message

pytestmark = pytest.mark.integration


@pytest.fixture()
def admin_headers(db):
    admin = Customer(email="admin@example.com", password_hash=hash_password("adminpass"), is_admin=1)
    db.add(admin)
    db.commit()
    return {"Authorization": f"Bearer {create_access_token(customer_id=admin.id)}"}


def _llm_reply(tool_name=None, args=None, content=None):
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


def _run(customer, conversation_id, message, category, llm_reply):
    from app.graph import run_supportflow

    with patch("app.graph.classify_ticket", return_value={
        "category": category, "priority": "MEDIUM", "sentiment": "NEUTRAL",
    }), patch("app.graph.chat_completion", return_value=llm_reply):
        return run_supportflow(
            customer_message=message,
            authenticated_customer_id=customer.id,
            conversation_id=conversation_id,
        )


@pytest.fixture()
def paused_refund(db, customer, orders):
    """A conversation whose run is paused on a $1,100 refund approval."""
    conversation = get_or_create_conversation(customer.id, db)
    state = _run(customer, conversation.id, "Refund $1100 for order 1006", "Refund Request",
                 _llm_reply("issue_refund", {"order_id": 1006, "amount": 1100.0}))
    assert state["approval_status"] == "PENDING_APPROVAL"
    assert "submitted for human approval" in state["final_response"]

    db.expire_all()
    approval = db.query(Approval).one()
    assert approval.graph_thread_id == f"conversation-{conversation.id}"
    assert approval.graph_checkpoint_id
    return {"conversation_id": conversation.id, "approval_id": approval.id, "action_id": approval.action_id}


def _completed_refunds(db):
    db.expire_all()
    return db.query(Action).filter_by(action_type="REFUND", reference_id=1006, status="COMPLETED").count()


def _assistant_messages(db, conversation_id):
    db.expire_all()
    return [m.content for m in db.query(Message).filter_by(conversation_id=conversation_id, role="assistant")]


def test_approve_resumes_and_executes_exactly_once(client, db, admin_headers, paused_refund):
    url = f"/approvals/{paused_refund['approval_id']}/approve"

    first = client.post(url, json={"reason": "verified"}, headers=admin_headers)
    assert first.status_code == 200
    body = first.json()
    assert body["executed"] is True and body["action_status"] == "COMPLETED"
    assert "approved and your $1,100.00 refund has been issued" in body["customer_reply"]
    assert _completed_refunds(db) == 1
    assert _assistant_messages(db, paused_refund["conversation_id"]) == [body["customer_reply"]]

    # Double approve is refused and resumes nothing
    second = client.post(url, json={"reason": "again"}, headers=admin_headers)
    assert second.status_code == 409
    assert _completed_refunds(db) == 1
    assert len(_assistant_messages(db, paused_refund["conversation_id"])) == 1


def test_reject_resumes_without_executing(client, db, admin_headers, paused_refund):
    response = client.post(f"/approvals/{paused_refund['approval_id']}/reject",
                           json={"reason": "suspicious"}, headers=admin_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["executed"] is False and body["action_status"] == "REJECTED"
    assert "not approved, so no refund has been issued" in body["customer_reply"]
    assert _completed_refunds(db) == 0
    assert _assistant_messages(db, paused_refund["conversation_id"]) == [body["customer_reply"]]


def test_resume_works_after_process_restart(client, db, admin_headers, paused_refund):
    # Drop the checkpointer's pool and the compiled graph: the next request
    # has only what is in Postgres, as after a restart.
    close_checkpointer()

    response = client.post(f"/approvals/{paused_refund['approval_id']}/approve",
                           json={"reason": "after restart"}, headers=admin_headers)

    assert response.status_code == 200
    assert "refund has been issued" in response.json()["customer_reply"]
    assert _completed_refunds(db) == 1


def test_resume_targets_the_paused_run_after_later_messages(client, db, customer, admin_headers, paused_refund):
    # The customer keeps talking on the same thread before the admin decides
    later = _run(customer, paused_refund["conversation_id"], "thanks, any update?", "Order Issue",
                 _llm_reply(content="It's still waiting for review."))
    assert later["final_response"] == "It's still waiting for review."

    response = client.post(f"/approvals/{paused_refund['approval_id']}/approve",
                           json={"reason": "ok"}, headers=admin_headers)

    assert "order #1006" in response.json()["customer_reply"]
    assert _completed_refunds(db) == 1


def test_approval_without_a_paused_run_still_works(client, db, customer, orders, admin_headers):
    """Approvals created outside the graph have no run to resume."""
    from app.approval import create_approval_request

    pending = create_approval_request("REFUND", 1006, customer.id, amount=1100.0, db=db)
    response = client.post(f"/approvals/{pending['approval_id']}/approve",
                           json={"reason": "ok"}, headers=admin_headers)

    assert response.status_code == 200
    assert response.json()["executed"] is True
    assert response.json()["customer_reply"] is None
