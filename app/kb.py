"""Knowledge Base module — seeding and search over knowledge_chunks.

Articles live in knowledge_base/*.md and are indexed as one chunk per
"## " section (app/retrieval.py). The tables are created by the migrate
service (app/migrate.py), which also seeds them.

Search pipeline, chosen by evaluation/retrieval_eval.py (results in
evaluation/results/retrieval.md):
  1. vector search for the RERANK_CANDIDATES nearest sections
  2. drop candidates under the similarity threshold (abstain if none are left;
     cosine similarity separates out-of-scope questions better than the
     cross-encoder score does)
  3. re-rank what is left with a cross-encoder (RAG_RERANK=0 turns this off)
"""
import os

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.embeddings import generate_embedding
from app.retrieval import (
    RERANK_CANDIDATES,
    chunk_article,
    index_chunks,
    load_articles,
    rerank,
    vector_search,
)

CHUNKING_STRATEGY = "section"


def rerank_enabled() -> bool:
    return os.getenv("RAG_RERANK", "1").lower() not in ("0", "false", "no")


def kb_titles() -> list[str]:
    """Titles of every knowledge_base/ article."""
    return [a.title for a in load_articles()]


def seed_knowledge_base(db: Session | None = None, force: bool = False) -> dict:
    """Indexes knowledge_base/ into knowledge_chunks if the table is empty.

    Args:
        db (Session, optional): SQLAlchemy DB session.
        force (bool): Re-index even when chunks already exist (after editing articles).

    Returns:
        dict: articles and chunks counts, and whether anything was indexed.
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        existing = db.execute(text("SELECT count(*) FROM knowledge_chunks")).scalar()
        articles = load_articles()
        if existing and not force:
            return {"articles": len(articles), "chunks": existing, "indexed": False}
        chunks = [c for a in articles for c in chunk_article(a, CHUNKING_STRATEGY)]
        index_chunks(db.connection(), chunks)
        db.commit()
        return {"articles": len(articles), "chunks": len(chunks), "indexed": True}
    except Exception:
        db.rollback()
        raise
    finally:
        if close_db:
            db.close()


def search_knowledge_base(
    query: str,
    top_k: int = 3,
    similarity_threshold: float | None = None,
    db: Session | None = None,
) -> list[dict]:
    """Retrieves the knowledge base sections most relevant to a query.

    Cosine similarity = 1 − pgvector cosine distance (<=>), in [-1, 1].

    Args:
        query:                Natural-language search query.
        top_k:                Maximum number of results to return. Must be >= 1.
        similarity_threshold: Optional minimum cosine similarity (0.0–1.0).
                              Sections below this threshold are excluded.
        db:                   SQLAlchemy DB session (created internally if None).

    Returns:
        list[dict]: Ranked sections, each containing id, title (the article
            title), heading, content, source, cosine_distance,
            cosine_similarity and rerank_score (None when re-ranking is off).

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

    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        hits = vector_search(
            db.connection(),
            query,
            max(top_k, RERANK_CANDIDATES),
            query_vector=generate_embedding(query),
        )
        if similarity_threshold is not None:
            hits = [h for h in hits if h.scores["vector"] >= similarity_threshold]
        if rerank_enabled():
            hits = rerank(query, hits)

        return [
            {
                "id": h.chunk_id,
                "title": h.doc_title,
                "heading": h.heading,
                "content": h.content,
                "source": h.source,
                "cosine_distance": round(1.0 - h.scores["vector"], 6),
                "cosine_similarity": round(h.scores["vector"], 6),
                "rerank_score": h.scores.get("rerank"),
            }
            for h in hits[:top_k]
        ]
    finally:
        if close_db:
            db.close()
