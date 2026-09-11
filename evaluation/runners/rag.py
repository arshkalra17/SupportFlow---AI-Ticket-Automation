"""
Runner — Metrics 3 & 4: RAG Hit@1, Hit@3, out-of-domain rejection.

Uses search_knowledge_base() from app.kb with the local all-MiniLM-L6-v2
model and pgvector.  Zero Groq calls.

The similarity threshold (RAG_SIMILARITY_THRESHOLD = 0.15) is applied
to filter results, matching the production threshold in app/rag.py.
Out-of-domain queries are expected to return an empty list after filtering.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from evaluation.config import (
    DATASETS_DIR,
    EVAL_DATABASE_URL,
    RAG_TOP_K,
    RAG_SIMILARITY_THRESHOLD,
)


def run() -> list[dict]:
    """
    Evaluates RAG retrieval on the rag_retrieval dataset.

    No Groq calls are made.  GROQ_API_KEY is not required.

    Returns:
        List of result dicts:
            {
              "id":           str,
              "query":        str,
              "gold_title":   str | None,
              "out_of_domain": bool,
              "retrieved":    list[dict],   # ranked, with cosine_similarity
            }
    """
    os.environ["DATABASE_URL"] = EVAL_DATABASE_URL

    from app.kb import search_knowledge_base

    dataset_path = DATASETS_DIR / "rag_retrieval.json"
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))

    results: list[dict] = []

    for case in dataset:
        case_id = case["id"]
        print(f"  [rag] {case_id}: retrieving ... ", end="", flush=True)

        retrieved = search_knowledge_base(
            query=case["query"],
            top_k=RAG_TOP_K,
            similarity_threshold=RAG_SIMILARITY_THRESHOLD,
        )

        top_title = retrieved[0]["title"] if retrieved else "(none)"
        hit = (
            "✓" if (not case["out_of_domain"] and retrieved
                    and retrieved[0]["title"] == case["gold_title"])
            else ("✓ (OOD rejected)" if case["out_of_domain"] and not retrieved
                  else "✗")
        )
        print(f"top={top_title!r} {hit}")

        results.append({
            "id":            case_id,
            "query":         case["query"],
            "gold_title":    case["gold_title"],
            "out_of_domain": case["out_of_domain"],
            "retrieved":     retrieved,
        })

    in_domain  = [r for r in results if not r["out_of_domain"]]
    out_domain = [r for r in results if r["out_of_domain"]]
    print(
        f"  [rag] done — {len(in_domain)} in-domain, "
        f"{len(out_domain)} out-of-domain, 0 Groq calls"
    )
    return results
