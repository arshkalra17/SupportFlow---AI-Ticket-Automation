"""Tests for Stage 12 Fix: Context-Aware Classification and Routing.

Tests the fix for implicit order references being misrouted to RAG instead of tool execution.
"""

import pytest
from unittest.mock import patch, MagicMock

from app.graph import run_supportflow, route_after_classify
from app.database import SessionLocal
from app.models import Customer, Order, Conversation, ConversationContext, Message
from app.conversation import (
    get_or_create_conversation,
    save_user_message,
    save_assistant_message,
    update_context,
    get_recent_messages,
)


@pytest.fixture
def test_customer(db):
    """Creates a test customer."""
    customer = Customer(
        email="context_test@example.com",
        password_hash="$2b$12$test_hash",
    )
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


@pytest.fixture
def test_orders(db, test_customer):
    """Creates test orders for the customer."""
    orders = [
        Order(
            id=1008,
            customer_id=test_customer.id,
            status="SHIPPED",
            total=99.99,
            items={"product": "Laptop"},
        ),
        Order(
            id=1011,
            customer_id=test_customer.id,
            status="DELIVERED",
            total=49.99,
            items={"product": "Mouse"},
        ),
    ]
    db.add_all(orders)
    db.commit()
    return orders


@pytest.mark.llm
def test_context_aware_classification_implicit_status_query(db, test_customer, test_orders):
    """Test: Implicit 'What's the status?' with active_order_id routes to tool execution."""
    # Setup: Create conversation with active order context
    conversation = get_or_create_conversation(test_customer.id, db)
    update_context(
        conversation.id,
        active_order_id=1011,
        last_action="get_order_status",
        db=db,
    )
    
    # Simulate previous conversation
    save_user_message(conversation.id, "Where is order 1011?", db)
    save_assistant_message(conversation.id, "Order 1011 has been delivered.", db)
    
    recent_messages = get_recent_messages(conversation.id, 10, db)
    
    # Action: Send implicit follow-up
    result = run_supportflow(
        customer_message="What's the status?",
        authenticated_customer_id=test_customer.id,
        conversation_id=conversation.id,
        conversation_history=recent_messages,
        active_order_id=1011,
        last_action="get_order_status",
    )
    
    # Assert: Should be classified as Order Issue and route to tool execution
    assert result["classification"] is not None
    category = result["classification"].get("category", "")
    
    # Classification might be "Order Issue" or safeguard might override
    # Either way, should have tool_calls
    assert len(result.get("tool_calls", [])) > 0, "Expected tool execution for implicit order reference"
    
    # Verify tool was called with correct order_id
    first_tool = result["tool_calls"][0]
    assert first_tool["tool_name"] == "get_order_status"
    assert first_tool["tool_args"]["order_id"] == 1011


@pytest.mark.llm
def test_context_switch_explicit_order_mention(db, test_customer, test_orders):
    """Test: 'Actually, check order 1011' then 'What's the status?' uses new context."""
    conversation = get_or_create_conversation(test_customer.id, db)
    
    # Turn 1: Ask about order 1008
    update_context(conversation.id, active_order_id=1008, last_action="get_order_status", db=db)
    save_user_message(conversation.id, "Where is order 1008?", db)
    save_assistant_message(conversation.id, "Order 1008 has been shipped.", db)
    
    # Turn 2: Switch context to order 1011
    result1 = run_supportflow(
        customer_message="Actually, check order 1011.",
        authenticated_customer_id=test_customer.id,
        conversation_id=conversation.id,
        conversation_history=get_recent_messages(conversation.id, 10, db),
        active_order_id=1008,  # Old context
        last_action="get_order_status",
    )
    
    # Verify context switched to 1011
    assert result1.get("active_order_id") == 1011
    
    # Update DB context
    update_context(conversation.id, active_order_id=1011, last_action="get_order_status", db=db)
    save_user_message(conversation.id, "Actually, check order 1011.", db)
    save_assistant_message(conversation.id, "Order 1011 has been delivered.", db)
    
    # Turn 3: Implicit reference should use new context (1011)
    result2 = run_supportflow(
        customer_message="What's the status?",
        authenticated_customer_id=test_customer.id,
        conversation_id=conversation.id,
        conversation_history=get_recent_messages(conversation.id, 10, db),
        active_order_id=1011,  # New context
        last_action="get_order_status",
    )
    
    # Assert: Should use order 1011
    assert len(result2.get("tool_calls", [])) > 0
    tool_call = result2["tool_calls"][-1]  # Last tool call
    assert tool_call["tool_args"]["order_id"] == 1011, "Should use new active_order_id (1011)"


@pytest.mark.llm
def test_implicit_cancel_request_with_context(db, test_customer, test_orders):
    """Test: 'Can I cancel it?' with active order context."""
    conversation = get_or_create_conversation(test_customer.id, db)
    update_context(conversation.id, active_order_id=1008, last_action="get_order_status", db=db)
    
    save_user_message(conversation.id, "Where is order 1008?", db)
    save_assistant_message(conversation.id, "Order 1008 has been shipped.", db)
    
    result = run_supportflow(
        customer_message="Can I cancel it?",
        authenticated_customer_id=test_customer.id,
        conversation_id=conversation.id,
        conversation_history=get_recent_messages(conversation.id, 10, db),
        active_order_id=1008,
        last_action="get_order_status",
    )
    
    # Should route to tool execution
    assert len(result.get("tool_calls", [])) > 0
    # May propose cancel_order or get_order_status first - either is acceptable
    # Just verify it routed to tools, not RAG


def test_routing_safeguard_override(db):
    """Test: Routing safeguard overrides misclassification when context exists."""
    state = {
        "customer_message": "What's the status?",
        "active_order_id": 1011,
        "last_action": "get_order_status",
        "classification": {
            "category": "General Inquiry",  # Misclassified
            "priority": "LOW",
            "sentiment": "NEUTRAL",
        },
        "error": None,
    }
    
    route = route_after_classify(state)
    
    # Safeguard should override and route to tool execution
    assert route == "tool_execution_node", "Should route to tools despite General Inquiry classification"


def test_routing_without_context_respects_classification(db):
    """Test: Without active context, General Inquiry routes to RAG."""
    state = {
        "customer_message": "What's the status?",
        "active_order_id": None,  # No context
        "last_action": None,
        "classification": {
            "category": "General Inquiry",
            "priority": "LOW",
            "sentiment": "NEUTRAL",
        },
        "error": None,
    }
    
    route = route_after_classify(state)
    
    # Without context, ambiguous message routes to RAG
    assert route == "retrieve_knowledge_node"


def test_routing_policy_question_always_goes_to_rag(db):
    """Test: True policy questions go to RAG even with order context."""
    state = {
        "customer_message": "What is your return policy?",
        "active_order_id": 1011,  # Has context but asking about policy
        "last_action": "get_order_status",
        "classification": {
            "category": "General Inquiry",
            "priority": "LOW",
            "sentiment": "NEUTRAL",
        },
        "error": None,
    }
    
    route = route_after_classify(state)
    
    # No order action keywords, should route to RAG
    assert route == "retrieve_knowledge_node"


def test_routing_explicit_order_id_overrides_context(db):
    """Test: Explicit order ID in message overrides active context."""
    state = {
        "customer_message": "What's the status of order 1020?",
        "active_order_id": 1011,  # Different context
        "last_action": "get_order_status",
        "classification": {
            "category": "Order Issue",
            "priority": "MEDIUM",
            "sentiment": "NEUTRAL",
        },
        "error": None,
    }
    
    route = route_after_classify(state)
    
    # Should route to tool execution (explicit order reference)
    assert route == "tool_execution_node"


@pytest.mark.llm
def test_customer_isolation_with_context(db):
    """Test: Customer cannot access another customer's order via context."""
    # Customer 1
    customer1 = Customer(email="customer1@test.com", password_hash="hash1")
    db.add(customer1)
    db.commit()
    db.refresh(customer1)
    
    # Customer 2
    customer2 = Customer(email="customer2@test.com", password_hash="hash2")
    db.add(customer2)
    db.commit()
    db.refresh(customer2)
    
    # Order belongs to customer1
    order = Order(
        id=2001,
        customer_id=customer1.id,
        status="SHIPPED",
        total=100.0,
        items={"product": "Item"},
    )
    db.add(order)
    db.commit()
    
    # Customer 2 tries to use context from customer 1's order
    conversation = get_or_create_conversation(customer2.id, db)
    
    result = run_supportflow(
        customer_message="What's the status?",
        authenticated_customer_id=customer2.id,  # Different customer
        conversation_id=conversation.id,
        conversation_history=[],
        active_order_id=2001,  # Belongs to customer1
        last_action="get_order_status",
    )
    
    # Tool should be called but authorization should fail
    if result.get("tool_calls"):
        tool_call = result["tool_calls"][-1]
        assert tool_call["authorization_passed"] is False, "Should not authorize cross-customer access"


def test_no_context_ambiguous_message_safe_behavior(db):
    """Test: Ambiguous message without context has safe behavior."""
    state = {
        "customer_message": "Can you help me?",
        "active_order_id": None,
        "last_action": None,
        "classification": {
            "category": "General Inquiry",
            "priority": "LOW",
            "sentiment": "NEUTRAL",
        },
        "error": None,
    }
    
    route = route_after_classify(state)
    
    # Should route to RAG (safe default for ambiguous queries)
    assert route == "retrieve_knowledge_node"
