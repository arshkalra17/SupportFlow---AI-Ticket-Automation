"""Knowledge base ingestion, chunking, fusion and re-ranking (app/retrieval.py)."""
from pathlib import Path

import pytest

from app import retrieval
from app.kb import kb_titles, search_knowledge_base
from app.retrieval import Hit, chunk_article, load_articles, parse_article, rrf_fuse


def _hit(chunk_id, slug="doc", score=0.0, **scores):
    return Hit(chunk_id, slug, slug.title(), "Heading", "content", "src", score, scores)


# ── Parsing and chunking ──────────────────────────────────────────────

def test_parse_article_splits_title_and_sections(tmp_path: Path):
    path = tmp_path / "sample.md"
    path.write_text("# Sample Policy\n\n## First\nOne.\nStill one.\n\n## Second\nTwo.\n")
    article = parse_article(path)
    assert article.slug == "sample"
    assert article.title == "Sample Policy"
    assert article.sections == [("First", "One.\nStill one."), ("Second", "Two.")]


def test_parse_article_requires_a_title(tmp_path: Path):
    path = tmp_path / "untitled.md"
    path.write_text("## Only a section\nText.\n")
    with pytest.raises(ValueError, match="Title"):
        parse_article(path)


def test_section_chunks_carry_article_context():
    article = load_articles()[0]
    chunks = chunk_article(article, "section")
    assert len(chunks) == len(article.sections)
    first = chunks[0]
    assert first.embedding_text().startswith(f"{article.title} — {first.heading}\n")


def test_document_chunk_is_whole_article():
    article = load_articles()[0]
    [chunk] = chunk_article(article, "document")
    assert chunk.heading is None
    assert all(heading in chunk.content for heading, _ in article.sections)


def test_unknown_chunking_strategy_rejected():
    with pytest.raises(ValueError):
        chunk_article(load_articles()[0], "sentence")


def test_core_policy_titles_are_kept():
    # Golden cases and the judge refer to these titles by name
    assert {
        "Return and Refund Policy",
        "Item Replacement Policy",
        "Shipping & Delivery Guidelines",
        "Order Cancellation Rules",
    } <= set(kb_titles())


# ── Fusion and re-ranking ─────────────────────────────────────────────

def test_rrf_rewards_agreement_between_lists():
    vector = [_hit(1, vector=0.9), _hit(2, vector=0.8), _hit(3, vector=0.7)]
    keyword = [_hit(2, keyword=5.0), _hit(3, keyword=4.0)]
    fused = rrf_fuse(vector, keyword)
    # 1 tops the vector list but appears in only one list, so it comes last
    assert [h.chunk_id for h in fused] == [2, 3, 1]
    assert fused[0].scores == {"vector": 0.8, "keyword": 5.0}


def test_rerank_reorders_by_cross_encoder_score(monkeypatch):
    class StubReranker:
        def predict(self, pairs):
            return [len(text) if "best" in text else 0 for _, text in pairs]

    monkeypatch.setattr(retrieval, "_reranker", StubReranker())
    hits = [_hit(1), _hit(2)]
    hits[1].content = "the best answer"
    ranked = retrieval.rerank("q", hits)
    assert [h.chunk_id for h in ranked] == [2, 1]
    assert ranked[0].scores["rerank"] > 0


# ── Against the indexed knowledge base ────────────────────────────────

@pytest.mark.slow
def test_keyword_search_ranks_rare_terms_with_bm25(seeded_kb, db):
    hits = retrieval.keyword_search(db.connection(), "express shipping to Hawaii", 5)
    assert hits[0].doc_slug == "shipping-and-delivery"
    assert hits[0].heading == "Delivery areas"


@pytest.mark.slow
def test_search_returns_sections_with_metadata(seeded_kb, db):
    [top] = search_knowledge_base("can I pay with Apple Pay", top_k=1, db=db)
    assert top["title"] == "Payments and Billing"
    assert top["heading"] == "Accepted payment methods"
    assert "Apple Pay" in top["content"]
    assert top["rerank_score"] is None  # RAG_RERANK=0 in tests


@pytest.mark.slow
def test_search_applies_reranker_after_threshold(seeded_kb, db, monkeypatch):
    seen = {}

    class StubReranker:
        def predict(self, pairs):
            seen["n"] = len(pairs)
            # Prefer the candidate the vector stage ranked last
            return list(range(len(pairs)))

    monkeypatch.setenv("RAG_RERANK", "1")
    monkeypatch.setattr(retrieval, "_reranker", StubReranker())
    vector_only = retrieval.vector_search(db.connection(), "is delivery free", retrieval.RERANK_CANDIDATES)
    results = search_knowledge_base("is delivery free", top_k=3, similarity_threshold=0.0, db=db)
    assert seen["n"] == retrieval.RERANK_CANDIDATES
    assert results[0]["id"] == vector_only[-1].chunk_id
    assert results[0]["rerank_score"] == retrieval.RERANK_CANDIDATES - 1
