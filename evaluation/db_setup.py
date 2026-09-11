"""
Evaluation database setup and teardown.

Creates the ORM schema in supportflow_eval, seeds synthetic evaluation
fixtures (customers + orders), and provides a matching teardown.

Rules:
- Only ever touches supportflow_eval — never the dev or test databases.
- Order IDs 5001–5022 are reserved for evaluation.
- Customer IDs 1–3 are synthetic; the customer table is truncated before
  seeding so there are no conflicts with prior runs.
- Teardown removes only the rows created here; it does not drop tables.
"""

from __future__ import annotations

import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

from evaluation.config import (
    EVAL_DATABASE_URL,
    EVAL_ORDER_IDS,
    EVAL_ORDER_STATUS,
    EVAL_CUSTOMER_IDS,
)


def _make_session() -> tuple[Session, any]:
    """Returns (session, engine) connected to the eval database."""
    engine = create_engine(EVAL_DATABASE_URL)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return SessionLocal(), engine


def setup_eval_db() -> Session:
    """
    Ensures the eval DB schema exists, then seeds synthetic fixtures.

    Returns a live Session for the caller to use.  The caller is
    responsible for closing it via teardown_eval_db().
    """
    # Import models here (not at module level) to avoid importing app
    # modules before DATABASE_URL is set in the caller's environment.
    os.environ["DATABASE_URL"] = EVAL_DATABASE_URL

    from app.database import Base
    from app.models import Customer, Order, KnowledgeDocument
    from app.embeddings import generate_embedding
    from app.kb import SEED_DOCUMENTS

    session, engine = _make_session()

    # Ensure pgvector extension and tables exist.
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
        conn.commit()
    Base.metadata.create_all(bind=engine)

    # ── Truncate eval-owned tables in dependency-safe order ──────────
    for table in [
        "idempotency_records", "approvals", "actions",
        "replacement_requests", "knowledge_documents",
        "orders", "tickets", "customers",
    ]:
        session.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
    session.commit()

    # ── Seed customers (IDs 1, 2, 3) ────────────────────────────────
    for cid in EVAL_CUSTOMER_IDS:
        session.execute(
            text(
                "INSERT INTO customers (id, email, password_hash, created_at) "
                "VALUES (:id, :email, :hash, NOW())"
            ),
            {
                "id":    cid,
                "email": f"eval_customer_{cid}@example.com",
                "hash":  "eval_hash_not_used",
            },
        )
    session.commit()

    # ── Seed orders ──────────────────────────────────────────────────
    for customer_id, order_ids in EVAL_ORDER_IDS.items():
        for order_id in order_ids:
            status = EVAL_ORDER_STATUS[order_id]
            session.execute(
                text(
                    "INSERT INTO orders (id, customer_id, status, created_at) "
                    "VALUES (:id, :cid, :status, NOW())"
                ),
                {"id": order_id, "cid": customer_id, "status": status},
            )
    session.commit()

    # ── Seed knowledge documents with embeddings ─────────────────────
    # The vector literal must be interpolated directly into the SQL string
    # because SQLAlchemy's text() bind-param handling conflicts with
    # PostgreSQL's ::vector cast syntax when using psycopg2.
    for doc in SEED_DOCUMENTS:
        vec = generate_embedding(f"{doc['title']}\n{doc['content']}")
        # Build a safe float-only literal — no user input, no injection risk.
        vec_literal = "[" + ",".join(f"{v:.8f}" for v in vec) + "]"
        session.execute(
            text(
                "INSERT INTO knowledge_documents "
                "(title, content, source, embedding, created_at) "
                "VALUES (:title, :content, :source, "
                f"'{vec_literal}'::vector, NOW())"
            ),
            {
                "title":   doc["title"],
                "content": doc["content"],
                "source":  doc["source"],
            },
        )
    session.commit()

    print(
        f"  [db_setup] eval DB ready — "
        f"{len(EVAL_CUSTOMER_IDS)} customers, "
        f"{sum(len(v) for v in EVAL_ORDER_IDS.values())} orders, "
        f"{len(SEED_DOCUMENTS)} knowledge documents"
    )
    return session


def teardown_eval_db(session: Session) -> None:
    """
    Cleans up all rows seeded by setup_eval_db().
    Does NOT drop tables.
    """
    for table in [
        "idempotency_records", "approvals", "actions",
        "replacement_requests", "knowledge_documents",
        "orders", "tickets", "customers",
    ]:
        session.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
    session.commit()
    session.close()
    print("  [db_setup] eval DB cleaned up")
