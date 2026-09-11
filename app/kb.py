"""Knowledge Base module — seed dataset and document management.

This module initializes the pgvector extension and manages KnowledgeDocument entries.
"""
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import SessionLocal, Base, engine
from app.models import KnowledgeDocument
from app.embeddings import generate_embedding, EMBEDDING_DIMENSION

# Ensure pgvector extension and tables exist
with engine.connect() as conn:
    conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
    conn.commit()

Base.metadata.create_all(bind=engine)


SEED_DOCUMENTS = [
    {
        "title": "Return and Refund Policy",
        "content": (
            "Customers may request a refund for eligible orders within 30 days of delivery. "
            "Refunds of $1000 or less are auto-approved by the system. "
            "Refunds exceeding $1000 require human admin approval before execution."
        ),
        "source": "policy_docs/returns.md",
    },
    {
        "title": "Item Replacement Policy",
        "content": (
            "Replacements can be requested for orders that arrive damaged, defective, or incorrect. "
            "Orders in PROCESSING, SHIPPED, or DELIVERED status are eligible for replacement requests."
        ),
        "source": "policy_docs/replacements.md",
    },
    {
        "title": "Shipping & Delivery Guidelines",
        "content": (
            "Standard shipping takes 3-5 business days. Express shipping takes 1-2 business days. "
            "Tracking numbers are generated once an order status updates to SHIPPED."
        ),
        "source": "policy_docs/shipping.md",
    },
    {
        "title": "Order Cancellation Rules",
        "content": (
            "Orders can only be cancelled while in PROCESSING status. "
            "Once an order has SHIPPED or DELIVERED, direct cancellation is disabled and "
            "the customer must initiate a return."
        ),
        "source": "policy_docs/cancellations.md",
    },
]


def seed_knowledge_documents(db: Session | None = None) -> list[dict]:
    """Seeds the initial synthetic support policy documents if the table is empty.

    Args:
        db (Session, optional): SQLAlchemy DB session.

    Returns:
        list[dict]: List of seeded or existing knowledge document records.
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        count = db.query(KnowledgeDocument).count()
        if count == 0:
            doc_objs = [
                KnowledgeDocument(
                    title=doc["title"],
                    content=doc["content"],
                    source=doc["source"],
                    embedding=None,  # Embeddings generated via populate_knowledge_document_embeddings
                )
                for doc in SEED_DOCUMENTS
            ]
            db.add_all(doc_objs)
            db.commit()
            for doc in doc_objs:
                db.refresh(doc)

        docs = db.query(KnowledgeDocument).all()
        return [
            {
                "id": d.id,
                "title": d.title,
                "content": d.content,
                "source": d.source,
                "has_embedding": d.embedding is not None,
                "created_at": str(d.created_at),
            }
            for d in docs
        ]
    finally:
        if close_db:
            db.close()


def embed_knowledge_documents(db: Session | None = None) -> dict:
    """Generates and persists embeddings for KnowledgeDocument records with NULL embeddings.

    Documents with existing embeddings are safely skipped to avoid redundant API/computation costs.

    Args:
        db (Session, optional): SQLAlchemy DB session.

    Returns:
        dict: Detailed log containing processed_count, skipped_count, and document status list.

    Raises:
        RuntimeError: If embedding generation or DB persistence fails for any document.
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        # Load only documents whose embedding is currently NULL
        pending_docs = (
            db.query(KnowledgeDocument)
            .filter(KnowledgeDocument.embedding == None)
            .all()
        )

        processed_count = 0
        for doc in pending_docs:
            text_to_embed = f"{doc.title}\n{doc.content}"
            try:
                embedding_vector = generate_embedding(text_to_embed)
            except Exception as err:
                db.rollback()
                raise RuntimeError(
                    f"Embedding generation failed for Document #{doc.id} ('{doc.title}'): {err}"
                ) from err

            if not isinstance(embedding_vector, list) or len(embedding_vector) != EMBEDDING_DIMENSION:
                db.rollback()
                raise ValueError(
                    f"Invalid vector dimension {len(embedding_vector) if isinstance(embedding_vector, list) else type(embedding_vector)} "
                    f"for Document #{doc.id}, expected {EMBEDDING_DIMENSION}"
                )

            doc.embedding = embedding_vector
            processed_count += 1

        if pending_docs:
            db.commit()

        # Query all documents to construct metadata report
        all_docs = db.query(KnowledgeDocument).all()
        skipped_count = len(all_docs) - processed_count

        doc_summaries = []
        for d in all_docs:
            vec_dim = len(d.embedding) if d.embedding is not None else 0
            doc_summaries.append({
                "id": d.id,
                "title": d.title,
                "has_embedding": d.embedding is not None,
                "vector_dimension": vec_dim,
            })

        return {
            "processed_count": processed_count,
            "skipped_count": skipped_count,
            "total_documents": len(all_docs),
            "documents": doc_summaries,
        }
    except Exception as err:
        if db:
            db.rollback()
        raise RuntimeError(f"Database embedding persistence failed: {err}") from err
    finally:
        if close_db:
            db.close()


def clear_knowledge_document_embeddings(db: Session | None = None) -> int:
    """Sets embedding = NULL for all KnowledgeDocument records to prepare for re-embedding.

    Args:
        db (Session, optional): SQLAlchemy DB session.

    Returns:
        int: Number of document records cleared.
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        updated = db.query(KnowledgeDocument).update({KnowledgeDocument.embedding: None})
        db.commit()
        return updated
    except Exception as err:
        if db:
            db.rollback()
        raise RuntimeError(f"Failed to clear knowledge document embeddings: {err}") from err
    finally:
        if close_db:
            db.close()


def search_knowledge_base(
    query: str,
    top_k: int = 3,
    similarity_threshold: float | None = None,
    db: Session | None = None,
) -> list[dict]:
    """Performs semantic vector retrieval over the knowledge base.

    Embeds the query using the same all-MiniLM-L6-v2 model that was used for
    the stored documents, then uses pgvector's cosine distance operator (<=>) to
    find the top-k most semantically similar KnowledgeDocument records.

    Distance metric:
        pgvector cosine distance (<=>) returns values in [0, 2]:
            0.0 = identical direction (perfect match)
            1.0 = orthogonal (unrelated)
            2.0 = opposite direction
        Cosine similarity = 1 − cosine_distance, giving values in [-1, 1]:
            1.0 = identical direction
            0.0 = orthogonal
           -1.0 = opposite direction

    Args:
        query:                Natural-language search query.
        top_k:                Maximum number of results to return. Must be >= 1.
        similarity_threshold: Optional minimum cosine similarity (0.0–1.0).
                              Documents below this threshold are excluded.
        db:                   SQLAlchemy DB session (created internally if None).

    Returns:
        list[dict]: Ranked list of matching documents, each containing:
            id, title, content, source, cosine_distance, cosine_similarity.

    Raises:
        ValueError: If query is empty or top_k < 1.
    """
    # ── Input validation ──────────────────────────────────────────────
    if not query or not isinstance(query, str) or not query.strip():
        raise ValueError("Search query must be a non-empty string")
    if not isinstance(top_k, int) or top_k < 1:
        raise ValueError(f"top_k must be an integer >= 1, got {top_k}")
    if similarity_threshold is not None:
        if not isinstance(similarity_threshold, (int, float)):
            raise ValueError("similarity_threshold must be a float")
        if not (0.0 <= similarity_threshold <= 1.0):
            raise ValueError(
                f"similarity_threshold must be between 0.0 and 1.0, got {similarity_threshold}"
            )

    # ── Session management ────────────────────────────────────────────
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        # ── Embed the query with the SAME model used for documents ────
        query_vector = generate_embedding(query)

        # ── pgvector cosine distance query ────────────────────────────
        # The <=> operator computes cosine distance.  We ORDER BY it ASC
        # so that the most-similar documents come first, then LIMIT to top_k.
        vector_literal = f"[{','.join(str(v) for v in query_vector)}]"

        sql = text(
            "SELECT id, title, content, source, "
            "       embedding <=> :vec AS cosine_distance "
            "FROM knowledge_documents "
            "WHERE embedding IS NOT NULL "
            "ORDER BY embedding <=> :vec ASC "
            "LIMIT :k"
        )

        rows = db.execute(sql, {"vec": vector_literal, "k": top_k}).fetchall()

        # ── Build result list with similarity scores ──────────────────
        results: list[dict] = []
        for row in rows:
            cosine_dist = float(row.cosine_distance)
            cosine_sim = 1.0 - cosine_dist

            # Apply optional similarity threshold
            if similarity_threshold is not None and cosine_sim < similarity_threshold:
                continue

            results.append({
                "id": row.id,
                "title": row.title,
                "content": row.content,
                "source": row.source,
                "cosine_distance": round(cosine_dist, 6),
                "cosine_similarity": round(cosine_sim, 6),
            })

        return results

    finally:
        if close_db:
            db.close()


# Alias for backward compatibility
populate_knowledge_document_embeddings = embed_knowledge_documents



