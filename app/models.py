from datetime import datetime
# pyrefly: ignore [missing-import]
from pgvector.sqlalchemy import Vector
# pyrefly: ignore [missing-import]
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from app.database import Base


class Ticket(Base):
    __tablename__ = "tickets"

    id = Column(Integer, primary_key=True, index=True)
    customer_message = Column(Text, nullable=False)
    status = Column(String, default="PENDING")
    category = Column(String, nullable=True)
    priority = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, nullable=False)
    status = Column(String, nullable=False, default="PROCESSING")
    created_at = Column(DateTime, default=datetime.utcnow)


class ReplacementRequest(Base):
    __tablename__ = "replacement_requests"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, nullable=False)
    customer_id = Column(Integer, nullable=False)
    status = Column(String, nullable=False, default="PENDING")
    created_at = Column(DateTime, default=datetime.utcnow)


class Action(Base):
    __tablename__ = "actions"

    id = Column(Integer, primary_key=True, index=True)
    action_type = Column(String, nullable=False)        # e.g. "REPLACEMENT_REQUEST", "REFUND"
    reference_id = Column(Integer, nullable=False)       # ID of the related entity (order_id)
    customer_id = Column(Integer, nullable=False)
    amount = Column(Float, nullable=True)               # e.g. refund amount
    status = Column(String, nullable=False, default="PENDING")   # PENDING / COMPLETED / REJECTED
    created_at = Column(DateTime, default=datetime.utcnow)


class Approval(Base):
    __tablename__ = "approvals"

    id = Column(Integer, primary_key=True, index=True)
    action_id = Column(Integer, ForeignKey("actions.id"), nullable=False)
    status = Column(String, nullable=False, default="PENDING")   # PENDING / APPROVED / REJECTED
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)
    resolved_by = Column(String, nullable=True)


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    source = Column(String, nullable=True)
    embedding = Column(Vector(384), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)



