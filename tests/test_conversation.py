"""
Stage 12 — Persistent Conversation Context Tests.

Tests conversation persistence, message history, context tracking,
multi-turn flows, customer isolation, and idempotency integration.
"""

import pytest
from unittest.mock import patch

from app.conversation import (
    get_or_create_conversation,
    get_recent_messages,
    save_user_message,
    save_assistant_message,
    get_or_create_context,
    update_context,
)
from app.models import Conversation, Message, ConversationContext


# ══════════════════════════════════════════════════════════════════════
# Conversation Management Tests
# ══════════════════════════════════════════════════════════════════════

class TestConversationManagement:
    """Tests for conversation creation and retrieval."""

    def test_get_or_create_conversation_creates_on_first_call(self, db, customer):
        """First call creates a new conversation."""
        conv = get_or_create_conversation(customer.id, db)
        assert conv.id is not None
        assert conv.customer_id == customer.id

    def test_get_or_create_conversation_reuses_existing(self, db, customer):
        """Second call returns the existing conversation."""
        conv1 = get_or_create_conversation(customer.id, db)
        conv2 = get_or_create_conversation(customer.id, db)
        assert conv1.id == conv2.id

    def test_two_customers_have_separate_conversations(self, db, customer, customer2):
        """Each customer has their own conversation."""
        conv1 = get_or_create_conversation(customer.id, db)
        conv2 = get_or_create_conversation(customer2.id, db)
        assert conv1.id != conv2.id
        assert conv1.customer_id == customer.id
        assert conv2.customer_id == customer2.id


# ══════════════════════════════════════════════════════════════════════
# Message Persistence Tests
# ══════════════════════════════════════════════════════════════════════

class TestMessagePersistence:
    """Tests for message saving and retrieval."""

    def test_save_user_message(self, db, conversation):
        """User message is saved correctly."""
        msg = save_user_message(conversation.id, "Where is my order?", db)
        assert msg.id is not None
        assert msg.conversation_id == conversation.id
        assert msg.role == "user"
        assert msg.content == "Where is my order?"

    def test_save_assistant_message(self, db, conversation):
        """Assistant message is saved correctly."""
        msg = save_assistant_message(conversation.id, "Your order is shipped.", db)
        assert msg.id is not None
        assert msg.conversation_id == conversation.id
        assert msg.role == "assistant"
        assert msg.content == "Your order is shipped."

    def test_messages_saved_in_order(self, db, conversation):
        """Messages preserve chronological order."""
        save_user_message(conversation.id, "Hello", db)
        save_assistant_message(conversation.id, "Hi there", db)
        save_user_message(conversation.id, "How are you?", db)

        messages = get_recent_messages(conversation.id, 10, db)
        # Most recent first
        assert messages[0]["content"] == "How are you?"
        assert messages[0]["role"] == "user"
        assert messages[1]["content"] == "Hi there"
        assert messages[1]["role"] == "assistant"
        assert messages[2]["content"] == "Hello"
        assert messages[2]["role"] == "user"

    def test_get_recent_messages_respects_limit(self, db, conversation):
        """Limit parameter controls number of messages returned."""
        for i in range(15):
            save_user_message(conversation.id, f"Message {i}", db)

        messages = get_recent_messages(conversation.id, 5, db)
        assert len(messages) == 5
        # Most recent should be "Message 14"
        assert messages[0]["content"] == "Message 14"

    def test_get_recent_messages_empty_conversation(self, db, conversation):
        """Empty conversation returns empty list."""
        messages = get_recent_messages(conversation.id, 10, db)
        assert messages == []


# ══════════════════════════════════════════════════════════════════════
# Context Management Tests
# ══════════════════════════════════════════════════════════════════════

class TestConversationContext:
    """Tests for conversation context persistence and updates."""

    def test_get_or_create_context_creates_on_first_call(self, db, conversation):
        """First call creates context with null values."""
        ctx = get_or_create_context(conversation.id, db)
        assert ctx.conversation_id == conversation.id
        assert ctx.active_order_id is None
        assert ctx.last_action is None

    def test_get_or_create_context_reuses_existing(self, db, conversation):
        """Second call returns existing context."""
        ctx1 = get_or_create_context(conversation.id, db)
        ctx2 = get_or_create_context(conversation.id, db)
        # Same record (primary key is conversation_id)
        assert ctx1.conversation_id == ctx2.conversation_id

    def test_update_active_order(self, db, conversation):
        """Active order ID is updated correctly."""
        update_context(conversation.id, active_order_id=1008, db=db)
        ctx = get_or_create_context(conversation.id, db)
        assert ctx.active_order_id == 1008

    def test_update_last_action(self, db, conversation):
        """Last action is updated correctly."""
        update_context(conversation.id, last_action="get_order_status", db=db)
        ctx = get_or_create_context(conversation.id, db)
        assert ctx.last_action == "get_order_status"

    def test_update_multiple_fields(self, db, conversation):
        """Multiple context fields updated simultaneously."""
        update_context(
            conversation.id,
            active_order_id=1015,
            last_action="issue_refund",
            db=db,
        )
        ctx = get_or_create_context(conversation.id, db)
        assert ctx.active_order_id == 1015
        assert ctx.last_action == "issue_refund"

    def test_context_updated_multiple_times(self, db, conversation):
        """Context can be updated multiple times."""
        update_context(conversation.id, active_order_id=1001, db=db)
        update_context(conversation.id, active_order_id=1008, db=db)
        update_context(conversation.id, active_order_id=1015, db=db)
        ctx = get_or_create_context(conversation.id, db)
        assert ctx.active_order_id == 1015


# ══════════════════════════════════════════════════════════════════════
# Customer Isolation Tests
# ══════════════════════════════════════════════════════════════════════

class TestCustomerIsolation:
    """Tests that conversation/context is properly isolated per customer."""

    def test_customer_cannot_access_other_customer_conversation(self, db, customer, customer2):
        """Customer 1 cannot retrieve customer 2's conversation."""
        conv1 = get_or_create_conversation(customer.id, db)
        conv2 = get_or_create_conversation(customer2.id, db)

        save_user_message(conv1.id, "Customer 1 message", db)
        save_user_message(conv2.id, "Customer 2 message", db)

        # Customer 1's messages
        messages1 = get_recent_messages(conv1.id, 10, db)
        assert len(messages1) == 1
        assert messages1[0]["content"] == "Customer 1 message"

        # Customer 2's messages
        messages2 = get_recent_messages(conv2.id, 10, db)
        assert len(messages2) == 1
        assert messages2[0]["content"] == "Customer 2 message"

    def test_two_customers_separate_contexts(self, db, customer, customer2):
        """Each customer has independent conversation context."""
        conv1 = get_or_create_conversation(customer.id, db)
        conv2 = get_or_create_conversation(customer2.id, db)

        update_context(conv1.id, active_order_id=1001, db=db)
        update_context(conv2.id, active_order_id=1004, db=db)

        ctx1 = get_or_create_context(conv1.id, db)
        ctx2 = get_or_create_context(conv2.id, db)

        assert ctx1.active_order_id == 1001
        assert ctx2.active_order_id == 1004


# ══════════════════════════════════════════════════════════════════════
# Integration: /support/process with Conversation
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestSupportProcessWithConversation:
    """Integration tests for /support/process with conversation persistence."""

    def _mock_state(self, final_response="Order status is SHIPPED", active_order_id=None):
        return {
            "final_response": final_response,
            "classification": {"category": "Order Issue", "priority": "LOW", "sentiment": "NEUTRAL"},
            "tool_calls": [],
            "approval_status": None,
            "retrieved_documents": [],
            "error": None,
            "active_order_id": active_order_id,
            "last_action": "get_order_status" if active_order_id else None,
        }

    def test_first_request_creates_conversation(self, client, customer, auth_headers, db):
        """First /support/process request creates a conversation."""
        with patch("app.main.run_supportflow", return_value=self._mock_state()):
            resp = client.post(
                "/support/process",
                json={"message": "Hello"},
                headers=auth_headers,
            )

        assert resp.status_code == 200

        # Verify conversation was created
        conv = db.query(Conversation).filter(Conversation.customer_id == customer.id).first()
        assert conv is not None

    def test_second_request_reuses_conversation(self, client, customer, auth_headers, db):
        """Second request reuses the same conversation."""
        with patch("app.main.run_supportflow", return_value=self._mock_state()):
            client.post("/support/process", json={"message": "First"}, headers=auth_headers)
            client.post("/support/process", json={"message": "Second"}, headers=auth_headers)

        # Should only have one conversation
        conversations = db.query(Conversation).filter(Conversation.customer_id == customer.id).all()
        assert len(conversations) == 1

    def test_messages_persist_after_request(self, client, customer, auth_headers, db):
        """Messages are saved to database after successful request."""
        with patch("app.main.run_supportflow", return_value=self._mock_state(final_response="Response")):
            resp = client.post(
                "/support/process",
                json={"message": "User message"},
                headers=auth_headers,
            )

        assert resp.status_code == 200

        # Verify messages were saved
        conv = db.query(Conversation).filter(Conversation.customer_id == customer.id).first()
        messages = db.query(Message).filter(Message.conversation_id == conv.id).order_by(Message.created_at).all()

        assert len(messages) == 2
        assert messages[0].role == "user"
        assert messages[0].content == "User message"
        assert messages[1].role == "assistant"
        assert messages[1].content == "Response"

    def test_active_order_persisted_after_tool_execution(self, client, customer, auth_headers, db):
        """Active order ID is persisted when tool execution succeeds."""
        with patch("app.main.run_supportflow", return_value=self._mock_state(active_order_id=1008)):
            resp = client.post(
                "/support/process",
                json={"message": "Status of order 1008?"},
                headers=auth_headers,
            )

        assert resp.status_code == 200

        # Verify context was updated
        conv = db.query(Conversation).filter(Conversation.customer_id == customer.id).first()
        ctx = db.query(ConversationContext).filter(ConversationContext.conversation_id == conv.id).first()

        assert ctx.active_order_id == 1008
        assert ctx.last_action == "get_order_status"

    def test_context_updated_when_order_changes(self, client, customer, auth_headers, db):
        """Context updates when a different order is referenced."""
        # First request: order 1008
        with patch("app.main.run_supportflow", return_value=self._mock_state(active_order_id=1008)):
            client.post(
                "/support/process",
                json={"message": "Status of order 1008?"},
                headers=auth_headers,
            )

        # Second request: order 1015
        with patch("app.main.run_supportflow", return_value=self._mock_state(active_order_id=1015)):
            client.post(
                "/support/process",
                json={"message": "Status of order 1015?"},
                headers=auth_headers,
            )

        # Verify context reflects latest order
        conv = db.query(Conversation).filter(Conversation.customer_id == customer.id).first()
        ctx = db.query(ConversationContext).filter(ConversationContext.conversation_id == conv.id).first()

        assert ctx.active_order_id == 1015


# ══════════════════════════════════════════════════════════════════════
# Idempotency Integration Tests
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestConversationIdempotency:
    """Tests that conversation persistence respects idempotency."""

    def _mock_state(self):
        return {
            "final_response": "Test response",
            "classification": {"category": "General Inquiry", "priority": "LOW", "sentiment": "NEUTRAL"},
            "tool_calls": [],
            "approval_status": None,
            "retrieved_documents": [],
            "error": None,
            "active_order_id": None,
            "last_action": None,
        }

    def test_replay_does_not_duplicate_messages(self, client, customer, auth_headers, db):
        """Replaying an idempotent request does not create duplicate messages."""
        with patch("app.main.run_supportflow", return_value=self._mock_state()):
            # First request
            client.post(
                "/support/process",
                json={"message": "Hello"},
                headers={**auth_headers, "Idempotency-Key": "test-key-123"},
            )

            # Replay
            resp = client.post(
                "/support/process",
                json={"message": "Hello"},
                headers={**auth_headers, "Idempotency-Key": "test-key-123"},
            )

        assert resp.status_code == 200
        assert resp.json()["idempotency_replayed"] is True

        # Verify only one set of messages
        conv = db.query(Conversation).filter(Conversation.customer_id == customer.id).first()
        messages = db.query(Message).filter(Message.conversation_id == conv.id).all()

        # Should have exactly 2 messages (user + assistant), not 4
        assert len(messages) == 2

    def test_replay_does_not_update_context_twice(self, client, customer, auth_headers, db):
        """Replaying does not mutate context twice."""
        state_with_order = {
            **self._mock_state(),
            "active_order_id": 1008,
            "last_action": "get_order_status",
        }

        with patch("app.main.run_supportflow", return_value=state_with_order):
            # First request
            client.post(
                "/support/process",
                json={"message": "Order 1008"},
                headers={**auth_headers, "Idempotency-Key": "order-key-456"},
            )

            # Verify initial context
            conv = db.query(Conversation).filter(Conversation.customer_id == customer.id).first()
            ctx = db.query(ConversationContext).filter(ConversationContext.conversation_id == conv.id).first()
            initial_updated_at = ctx.updated_at

            # Replay
            client.post(
                "/support/process",
                json={"message": "Order 1008"},
                headers={**auth_headers, "Idempotency-Key": "order-key-456"},
            )

            # Context should not have changed
            db.refresh(ctx)
            assert ctx.active_order_id == 1008
            assert ctx.updated_at == initial_updated_at  # No update on replay


# ══════════════════════════════════════════════════════════════════════
# Multi-Turn Flow Tests (Live LLM)
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.llm
@pytest.mark.integration
class TestMultiTurnConversation:
    """Live multi-turn conversation tests with real Groq API."""

    def test_explicit_order_then_implicit_reference(self, client, customer, orders, auth_headers, db):
        """
        Turn 1: "Where is order 1001?"
        Turn 2: "Can I cancel it?"
        
        The second request should use active_order_id=1001 from context.
        """
        # Turn 1: Explicit order reference
        resp1 = client.post(
            "/support/process",
            json={"message": "Where is order 1001?"},
            headers=auth_headers,
        )
        assert resp1.status_code == 200

        # Verify context was set
        conv = db.query(Conversation).filter(Conversation.customer_id == customer.id).first()
        ctx = db.query(ConversationContext).filter(ConversationContext.conversation_id == conv.id).first()
        assert ctx.active_order_id == 1001

        # Turn 2: Implicit reference ("it")
        resp2 = client.post(
            "/support/process",
            json={"message": "Can I cancel it?"},
            headers=auth_headers,
        )
        assert resp2.status_code == 200

        # Context should still be 1001
        db.refresh(ctx)
        assert ctx.active_order_id == 1001

    def test_order_switch_updates_context(self, client, customer, orders, auth_headers, db):
        """
        Turn 1: "Where is order 1001?"
        Turn 2: "Actually, check order 1003."
        Turn 3: "Can I cancel it?"
        
        The third request should resolve to order 1003.
        """
        # Turn 1
        client.post(
            "/support/process",
            json={"message": "Where is order 1001?"},
            headers=auth_headers,
        )

        # Turn 2: Switch to different order
        client.post(
            "/support/process",
            json={"message": "Actually, what about order 1003?"},
            headers=auth_headers,
        )

        # Verify context switched to 1003
        conv = db.query(Conversation).filter(Conversation.customer_id == customer.id).first()
        ctx = db.query(ConversationContext).filter(ConversationContext.conversation_id == conv.id).first()
        assert ctx.active_order_id == 1003

        # Turn 3: Implicit reference should use 1003
        resp3 = client.post(
            "/support/process",
            json={"message": "What's the status?"},
            headers=auth_headers,
        )
        assert resp3.status_code == 200

    def test_unauthorized_order_does_not_update_context(self, client, customer, orders, auth_headers, db):
        """Attempting to access an unauthorized order must not update context."""
        # Try to access order 1004 (owned by customer2)
        resp = client.post(
            "/support/process",
            json={"message": "Where is order 1004?"},
            headers=auth_headers,
        )
        assert resp.status_code == 200

        # Verify authorization failed in tool_calls
        tool_calls = resp.json().get("tool_calls", [])
        if tool_calls:
            assert any(not tc.get("authorization_passed", True) for tc in tool_calls)

        # Context should NOT have been updated to 1004
        conv = db.query(Conversation).filter(Conversation.customer_id == customer.id).first()
        ctx = db.query(ConversationContext).filter(ConversationContext.conversation_id == conv.id).first()
        assert ctx.active_order_id != 1004
