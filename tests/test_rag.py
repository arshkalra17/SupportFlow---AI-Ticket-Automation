"""
Stage 8 — RAG retrieval tests (deterministic, no Groq required).

Uses the real local all-MiniLM-L6-v2 model for embedding.
Tests retrieval quality WITHOUT calling the Groq LLM for generation.

Gold dataset — query → expected top document title:
    "package arrived damaged"      → Item Replacement Policy
    "eligible for a refund"        → Return and Refund Policy
    "express delivery time"        → Shipping & Delivery Guidelines
    "cancel my processing order"   → Order Cancellation Rules
    "capital of France"            → (out-of-domain, below threshold)

Assertions:
    Hit@1 — top-1 result matches expected document
    Hit@3 — expected document appears in top-3 results
    Out-of-domain — similarity below RAG_SIMILARITY_THRESHOLD
    Threshold filtering — low-similarity docs excluded from results
"""

import pytest
from app.kb import search_knowledge_base
from app.rag import RAG_SIMILARITY_THRESHOLD


# ══════════════════════════════════════════════════════════════════════
# Gold retrieval dataset
# ══════════════════════════════════════════════════════════════════════

GOLD_DATASET = [
    {
        "query": "my package arrived damaged, I need a replacement",
        "expected_title": "Item Replacement Policy",
        "description": "damaged package → replacement policy",
    },
    {
        "query": "I want to get a refund for my recent purchase",
        "expected_title": "Return and Refund Policy",
        "description": "refund request → refund policy",
    },
    {
        "query": "how long does express shipping take",
        "expected_title": "Shipping & Delivery Guidelines",
        "description": "shipping time inquiry → shipping guidelines",
    },
    {
        "query": "can I cancel an order that is still being processed",
        "expected_title": "Order Cancellation Rules",
        "description": "cancel processing order → cancellation rules",
    },
]

OUT_OF_DOMAIN_QUERIES = [
    "what is the capital of France",
    "who won the 2022 World Cup",
    "recipe for chocolate cake",
]


# ══════════════════════════════════════════════════════════════════════
# Retrieval Hit@1 tests (slow — involves embedding model)
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.slow
@pytest.mark.parametrize("case", GOLD_DATASET, ids=[c["description"] for c in GOLD_DATASET])
def test_hit_at_1(seeded_kb, db, case):
    """Top-1 retrieved document must match the expected policy."""
    results = search_knowledge_base(
        query=case["query"],
        top_k=3,
        db=db,
    )
    assert len(results) >= 1, f"Expected at least 1 result for: {case['query']!r}"
    top_title = results[0]["title"]
    assert top_title == case["expected_title"], (
        f"Hit@1 FAIL for {case['description']!r}: "
        f"expected {case['expected_title']!r}, got {top_title!r} "
        f"(similarity={results[0]['cosine_similarity']:.4f})"
    )


@pytest.mark.slow
@pytest.mark.parametrize("case", GOLD_DATASET, ids=[c["description"] for c in GOLD_DATASET])
def test_hit_at_3(seeded_kb, db, case):
    """Expected document must appear somewhere in the top-3 results."""
    results = search_knowledge_base(
        query=case["query"],
        top_k=3,
        db=db,
    )
    titles = [r["title"] for r in results]
    assert case["expected_title"] in titles, (
        f"Hit@3 FAIL for {case['description']!r}: "
        f"expected {case['expected_title']!r} in top-3, got {titles}"
    )


# ══════════════════════════════════════════════════════════════════════
# Out-of-domain queries
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.slow
@pytest.mark.parametrize("query", OUT_OF_DOMAIN_QUERIES)
def test_out_of_domain_below_threshold(seeded_kb, db, query):
    """
    Out-of-domain queries must return no results when the similarity
    threshold is applied.  This verifies our RAG_SIMILARITY_THRESHOLD
    prevents fabricated policy answers.
    """
    results = search_knowledge_base(
        query=query,
        top_k=3,
        similarity_threshold=RAG_SIMILARITY_THRESHOLD,
        db=db,
    )
    assert len(results) == 0, (
        f"Out-of-domain query {query!r} unexpectedly returned results: "
        + ", ".join(f"{r['title']} ({r['cosine_similarity']:.4f})" for r in results)
    )


@pytest.mark.slow
def test_similarity_scores_are_ordered_descending(seeded_kb, db):
    """Results must be sorted highest-similarity-first."""
    results = search_knowledge_base(
        query="I need a replacement for my broken order",
        top_k=4,
        db=db,
    )
    sims = [r["cosine_similarity"] for r in results]
    assert sims == sorted(sims, reverse=True), (
        f"Results not sorted by descending similarity: {sims}"
    )


@pytest.mark.slow
def test_top_k_limits_results(seeded_kb, db):
    """top_k parameter must limit the number of returned documents."""
    results_1 = search_knowledge_base(query="refund", top_k=1, db=db)
    results_3 = search_knowledge_base(query="refund", top_k=3, db=db)
    assert len(results_1) <= 1
    assert len(results_3) <= 3


@pytest.mark.slow
def test_similarity_threshold_filters_low_scores(seeded_kb, db):
    """High threshold (0.99) should return no results for any query."""
    results = search_knowledge_base(
        query="shipping time",
        top_k=3,
        similarity_threshold=0.99,
        db=db,
    )
    assert all(r["cosine_similarity"] >= 0.99 for r in results)


# ══════════════════════════════════════════════════════════════════════
# Input validation on search_knowledge_base
# ══════════════════════════════════════════════════════════════════════

def test_empty_query_raises_value_error(seeded_kb, db):
    with pytest.raises(ValueError, match="non-empty"):
        search_knowledge_base(query="", db=db)


def test_invalid_top_k_raises_value_error(seeded_kb, db):
    with pytest.raises(ValueError):
        search_knowledge_base(query="refund", top_k=0, db=db)


def test_invalid_similarity_threshold_raises_value_error(seeded_kb, db):
    with pytest.raises(ValueError):
        search_knowledge_base(
            query="refund", top_k=3, similarity_threshold=1.5, db=db
        )


# ══════════════════════════════════════════════════════════════════════
# Embedding generation
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.slow
class TestEmbeddingGeneration:
    def test_embedding_returns_correct_dimension(self):
        from app.embeddings import generate_embedding, EMBEDDING_DIMENSION
        vec = generate_embedding("test document about shipping")
        assert len(vec) == EMBEDDING_DIMENSION

    def test_embedding_returns_floats(self):
        from app.embeddings import generate_embedding
        vec = generate_embedding("customer wants refund")
        assert all(isinstance(v, float) for v in vec)

    def test_empty_text_raises_value_error(self):
        from app.embeddings import generate_embedding
        with pytest.raises(ValueError):
            generate_embedding("")

    def test_same_text_same_embedding(self):
        from app.embeddings import generate_embedding
        v1 = generate_embedding("deterministic embedding check")
        v2 = generate_embedding("deterministic embedding check")
        assert v1 == v2

    def test_different_texts_different_embeddings(self):
        from app.embeddings import generate_embedding
        v1 = generate_embedding("package arrived damaged")
        v2 = generate_embedding("capital of France is Paris")
        assert v1 != v2
