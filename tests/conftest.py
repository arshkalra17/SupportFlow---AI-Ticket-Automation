"""
Shared pytest fixtures for the SupportFlow test suite.

Isolation strategy
──────────────────
- All tests run against `supportflow_test` (a dedicated test database,
  separate from the development `supportflow` database).
- Each test receives a fresh session via a per-test TRUNCATE of all tables,
  which is faster than drop/create and handles FK ordering automatically.
- The test DATABASE_URL is injected via os.environ BEFORE any app module
  is imported so that app/database.py picks it up correctly.

fakeredis / redis-py compatibility
────────────────────────────────────
- Production redis-py: 8.1.0  (unchanged)
- Test fakeredis:       2.38.0 (test-only, in requirements-test.txt)
- fakeredis <2.28 does not implement the HELLO handshake that redis-py 8.x
  sends on every new connection (RESP3 protocol).  fakeredis >=2.28 does.
  No production dependency is changed.

Real-Redis fixture
──────────────────
- `real_redis` connects to the Docker Redis at localhost:6379.
- Marked @pytest.mark.integration so it is only exercised when the
  Docker stack is available.
"""

import os
import pytest

# ── Inject test DATABASE_URL BEFORE any app import ───────────────────
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql://supportflow_user:supportflow_password@localhost:5432/supportflow_test",
)
os.environ.setdefault("JWT_SECRET", "test_secret_key_stage8_supportflow")

# Now safe to import app modules
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Customer, Order, KnowledgeDocument
from app.auth import hash_password, create_access_token
from app.embeddings import generate_embedding

# ── Test database engine ──────────────────────────────────────────────
TEST_DATABASE_URL = os.environ["DATABASE_URL"]

test_engine = create_engine(TEST_DATABASE_URL)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


# ── Session-scoped: create all tables once per test run ───────────────
@pytest.fixture(scope="session", autouse=True)
def create_test_tables():
    """Creates all ORM tables in the test database once per session."""
    with test_engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
        conn.commit()
    Base.metadata.create_all(bind=test_engine)
    yield


# ── Per-test DB fixture ───────────────────────────────────────────────
@pytest.fixture()
def db():
    """
    Provides a clean SQLAlchemy Session for each test.

    Truncates all application tables before every test (children-first
    FK order) so each test starts with an empty slate.
    """
    session = TestSessionLocal()

    truncate_order = [
        "idempotency_records",
        "approvals",
        "actions",
        "replacement_requests",
        "knowledge_documents",
        "orders",
        "tickets",
        "customers",
    ]
    for table in truncate_order:
        session.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
    session.commit()

    yield session
    session.close()


# ── Customer fixtures ─────────────────────────────────────────────────
@pytest.fixture()
def customer(db):
    """Creates and returns a single test customer."""
    c = Customer(email="alice@example.com", password_hash=hash_password("password123"))
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


@pytest.fixture()
def customer2(db, customer):
    """Creates a second customer for cross-tenant isolation tests."""
    c = Customer(email="bob@example.com", password_hash=hash_password("password456"))
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


@pytest.fixture()
def jwt_token(customer):
    """Returns a valid JWT token for `customer`."""
    return create_access_token(customer_id=customer.id)


@pytest.fixture()
def jwt_token2(customer2):
    """Returns a valid JWT token for `customer2`."""
    return create_access_token(customer_id=customer2.id)


@pytest.fixture()
def auth_headers(jwt_token):
    """Returns Authorization headers dict for `customer`."""
    return {"Authorization": f"Bearer {jwt_token}"}


@pytest.fixture()
def auth_headers2(jwt_token2):
    """Returns Authorization headers dict for `customer2`."""
    return {"Authorization": f"Bearer {jwt_token2}"}


# ── Order fixtures ────────────────────────────────────────────────────
@pytest.fixture()
def orders(db, customer, customer2):
    """
    Seeds a small set of orders covering different states and owners:

        1001  customer   SHIPPED
        1002  customer   DELIVERED
        1003  customer   PROCESSING
        1004  customer2  DELIVERED   ← owned by customer2, not customer
        1005  customer   CANCELLED
        1006  customer   DELIVERED   ← used for refund tests
    """
    rows = [
        Order(id=1001, customer_id=customer.id, status="SHIPPED"),
        Order(id=1002, customer_id=customer.id, status="DELIVERED"),
        Order(id=1003, customer_id=customer.id, status="PROCESSING"),
        Order(id=1004, customer_id=customer2.id, status="DELIVERED"),
        Order(id=1005, customer_id=customer.id, status="CANCELLED"),
        Order(id=1006, customer_id=customer.id, status="DELIVERED"),
    ]
    db.add_all(rows)
    db.commit()
    return rows


# ── Knowledge base fixture ────────────────────────────────────────────
@pytest.fixture(scope="session")
def _kb_embeddings():
    """
    Computes embeddings for the 4 policy documents once per session.
    SentenceTransformer model load is expensive; session scope avoids
    reloading it for every test.
    """
    from app.kb import SEED_DOCUMENTS

    embedded = []
    for doc in SEED_DOCUMENTS:
        vec = generate_embedding(f"{doc['title']}\n{doc['content']}")
        embedded.append({**doc, "embedding": vec})
    return embedded


@pytest.fixture()
def seeded_kb(db, _kb_embeddings):
    """
    Inserts the 4 policy KnowledgeDocuments with pre-computed embeddings
    into the (already-truncated) test database.
    """
    docs = [
        KnowledgeDocument(
            title=d["title"],
            content=d["content"],
            source=d["source"],
            embedding=d["embedding"],
        )
        for d in _kb_embeddings
    ]
    db.add_all(docs)
    db.commit()
    return docs


# ── FastAPI test client ───────────────────────────────────────────────
@pytest.fixture()
def client(db):
    """
    Returns a FastAPI TestClient wired to the test database session.

    Overrides `get_db` so every HTTP request uses the same (already-
    truncated and seeded) test session as the test body.
    """
    from fastapi.testclient import TestClient
    from app.main import app
    from app.database import get_db

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()


# ── fakeredis fixture (unit / isolation tests) ────────────────────────
@pytest.fixture()
def fake_redis(monkeypatch):
    """
    Replaces the live Redis client in app.queue and app.worker with a
    fakeredis in-memory instance.

    Compatibility: fakeredis 2.38.0 + redis-py 8.1.0.
    fakeredis >=2.28 implements the HELLO handshake required by redis-py
    8.x RESP3 protocol; earlier versions did not.
    No production dependency is changed.
    """
    import fakeredis
    import app.queue as queue_module
    import app.worker as worker_module

    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(queue_module, "redis_client", fake)
    monkeypatch.setattr(worker_module, "redis_client", fake)
    return fake


# ── Real Redis fixture (integration, requires Docker stack) ───────────
@pytest.fixture()
def real_redis():
    """
    Connects to the actual Docker Redis at localhost:6379 and flushes
    the test queue key before and after the test.

    Marked @pytest.mark.integration — skipped automatically unless the
    Docker stack is running.

    Purpose: verifies the actual production Redis code path (rpush,
    blpop, llen, lpop) independently of fakeredis.
    """
    import redis as redis_lib
    from app.queue import QUEUE_NAME

    try:
        client = redis_lib.Redis(
            host=os.environ.get("REDIS_HOST", "localhost"),
            port=int(os.environ.get("REDIS_PORT", "6379")),
            db=0,
            decode_responses=True,
            socket_connect_timeout=2,
        )
        client.ping()
    except Exception:
        pytest.skip("Docker Redis not available at localhost:6379")

    # Clean the test queue key before the test
    client.delete(QUEUE_NAME)
    yield client
    # Clean up after
    client.delete(QUEUE_NAME)
