"""Conversation and context management for SupportFlow.

Provides persistent conversation state across successive /support/process requests:
- Conversation: one per customer
- Message: user/assistant message history
- ConversationContext: active order/ticket/action tracking

All functions are scoped to authenticated customer identity.
"""

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal
from app.models import Conversation, Message, ConversationContext
from app.observability import traced_span, safe_set_attribute


def get_or_create_conversation(customer_id: int, db: Session | None = None) -> Conversation:
    """Gets or creates the customer's conversation.

    Args:
        customer_id: Authenticated customer ID (trusted identity from JWT).
        db: Optional SQLAlchemy session.

    Returns:
        Conversation instance (existing or newly created).
    """
    with traced_span("conversation.get_or_create") as span:
        safe_set_attribute(span, "customer_id", str(customer_id))

        close_db = False
        if db is None:
            db = SessionLocal()
            close_db = True

        try:
            # Try to find existing conversation
            conversation = db.query(Conversation).filter(
                Conversation.customer_id == customer_id
            ).first()

            if conversation:
                safe_set_attribute(span, "conversation.created", False)
                safe_set_attribute(span, "conversation.id", conversation.id)
                return conversation

            # Create new conversation
            conversation = Conversation(customer_id=customer_id)
            db.add(conversation)
            try:
                db.commit()
                db.refresh(conversation)
                safe_set_attribute(span, "conversation.created", True)
                safe_set_attribute(span, "conversation.id", conversation.id)
                return conversation
            except IntegrityError:
                # Race condition: another request created it first
                db.rollback()
                conversation = db.query(Conversation).filter(
                    Conversation.customer_id == customer_id
                ).first()
                if conversation:
                    safe_set_attribute(span, "conversation.created", False)
                    safe_set_attribute(span, "conversation.race_resolved", True)
                    return conversation
                raise  # Should not happen

        finally:
            if close_db:
                db.close()


def get_recent_messages(
    conversation_id: int,
    limit: int = 10,
    db: Session | None = None
) -> list[dict]:
    """Retrieves recent messages for context, ordered by created_at descending.

    Args:
        conversation_id: Conversation ID.
        limit: Maximum number of messages to retrieve.
        db: Optional SQLAlchemy session.

    Returns:
        List of message dicts with keys: role, content, created_at (ISO format).
        Most recent message is FIRST in the list.
    """
    with traced_span("conversation.get_recent_messages") as span:
        safe_set_attribute(span, "conversation_id", conversation_id)
        safe_set_attribute(span, "limit", limit)

        close_db = False
        if db is None:
            db = SessionLocal()
            close_db = True

        try:
            messages = (
                db.query(Message)
                .filter(Message.conversation_id == conversation_id)
                .order_by(Message.created_at.desc())
                .limit(limit)
                .all()
            )

            safe_set_attribute(span, "messages_retrieved", len(messages))

            # Return in reverse chronological order (most recent first)
            result = [
                {
                    "role": msg.role,
                    "content": msg.content,
                    "created_at": msg.created_at.isoformat(),
                }
                for msg in messages
            ]
            return result

        finally:
            if close_db:
                db.close()


def save_user_message(
    conversation_id: int,
    content: str,
    db: Session | None = None
) -> Message:
    """Saves a user message to the conversation.

    Args:
        conversation_id: Conversation ID.
        content: User message content.
        db: Optional SQLAlchemy session.

    Returns:
        Created Message instance.
    """
    with traced_span("conversation.save_user_message") as span:
        safe_set_attribute(span, "conversation_id", conversation_id)

        close_db = False
        if db is None:
            db = SessionLocal()
            close_db = True

        try:
            message = Message(
                conversation_id=conversation_id,
                role="user",
                content=content,
            )
            db.add(message)
            db.commit()
            db.refresh(message)

            safe_set_attribute(span, "message_id", message.id)
            return message

        finally:
            if close_db:
                db.close()


def save_assistant_message(
    conversation_id: int,
    content: str,
    db: Session | None = None
) -> Message:
    """Saves an assistant message to the conversation.

    Args:
        conversation_id: Conversation ID.
        content: Assistant response content.
        db: Optional SQLAlchemy session.

    Returns:
        Created Message instance.
    """
    with traced_span("conversation.save_assistant_message") as span:
        safe_set_attribute(span, "conversation_id", conversation_id)

        close_db = False
        if db is None:
            db = SessionLocal()
            close_db = True

        try:
            message = Message(
                conversation_id=conversation_id,
                role="assistant",
                content=content,
            )
            db.add(message)
            db.commit()
            db.refresh(message)

            safe_set_attribute(span, "message_id", message.id)
            return message

        finally:
            if close_db:
                db.close()


def get_or_create_context(
    conversation_id: int,
    db: Session | None = None
) -> ConversationContext:
    """Gets or creates conversation context.

    Args:
        conversation_id: Conversation ID.
        db: Optional SQLAlchemy session.

    Returns:
        ConversationContext instance.
    """
    with traced_span("conversation.get_or_create_context") as span:
        safe_set_attribute(span, "conversation_id", conversation_id)

        close_db = False
        if db is None:
            db = SessionLocal()
            close_db = True

        try:
            context = db.query(ConversationContext).filter(
                ConversationContext.conversation_id == conversation_id
            ).first()

            if context:
                safe_set_attribute(span, "context.created", False)
                return context

            # Create new context
            context = ConversationContext(conversation_id=conversation_id)
            db.add(context)
            db.commit()
            db.refresh(context)

            safe_set_attribute(span, "context.created", True)
            return context

        finally:
            if close_db:
                db.close()


def update_context(
    conversation_id: int,
    active_order_id: int | None = None,
    active_ticket_id: int | None = None,
    last_action: str | None = None,
    db: Session | None = None
) -> ConversationContext:
    """Updates conversation context fields.

    Args:
        conversation_id: Conversation ID.
        active_order_id: Order ID to set as active (or None to clear).
        active_ticket_id: Ticket ID to set as active (or None to clear).
        last_action: Last action performed (e.g., "get_order_status").
        db: Optional SQLAlchemy session.

    Returns:
        Updated ConversationContext instance.
    """
    with traced_span("conversation.update_context") as span:
        safe_set_attribute(span, "conversation_id", conversation_id)
        if active_order_id is not None:
            safe_set_attribute(span, "active_order_id", active_order_id)
        if last_action is not None:
            safe_set_attribute(span, "last_action", last_action)

        close_db = False
        if db is None:
            db = SessionLocal()
            close_db = True

        try:
            context = get_or_create_context(conversation_id, db)

            # Update only non-None values
            if active_order_id is not None:
                context.active_order_id = active_order_id
            if active_ticket_id is not None:
                context.active_ticket_id = active_ticket_id
            if last_action is not None:
                context.last_action = last_action

            db.commit()
            db.refresh(context)

            return context

        finally:
            if close_db:
                db.close()
