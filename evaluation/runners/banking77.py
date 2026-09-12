"""
Runner — BANKING77 External Category Benchmark.

This is a CROSS-DOMAIN EXTERNAL CATEGORY BENCHMARK.

It measures category/intent classification generalization on real banking
customer service queries from the BANKING77 dataset.

IMPORTANT:
This benchmark measures ONLY category classification.
It does NOT measure:
- priority
- sentiment
- tool selection
- authorization
- approval routing
- RAG

Those remain SupportFlow-specific evaluations.

Calls classify_ticket() from app.llm sequentially.
Caches every result immediately keyed by case_id + model + prompt_version.
Skips cases already in cache (resume-safe).

Groq calls: up to 154 on a completely uncached run, 0 on a fully-cached run.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from evaluation.cache import EvalCache
from evaluation.config import (
    DATASETS_DIR,
    EVAL_MODEL,
    CLASSIFICATION_PROMPT_VERSION,
    GROQ_CALL_DELAY_SECONDS,
)


def run(no_cache: bool = False) -> list[dict]:
    """
    Evaluates the classifier on the BANKING77 external benchmark.

    Args:
        no_cache: If True, ignores and clears any existing cache.

    Returns:
        List of result dicts, one per benchmark case:
            {
              "id":                       str,
              "text":                     str,
              "original_intent":          str,
              "gold_supportflow_category": str | None,
              "mapping_ambiguous":        bool,
              "predicted_category":       str | None,
              "from_cache":               bool,
              "error":                    str | None,
            }
    """
    # Guard: we need GROQ_API_KEY for this runner.
    if not os.getenv("GROQ_API_KEY"):
        raise EnvironmentError(
            "GROQ_API_KEY is not set. "
            "BANKING77 runner requires a live Groq API key."
        )

    # Import here so DATABASE_URL is already set by the time app.llm loads.
    from app.llm import classify_ticket

    benchmark_path = DATASETS_DIR / "banking77_benchmark.json"
    benchmark_data = json.loads(benchmark_path.read_text(encoding="utf-8"))
    cases = benchmark_data["cases"]

    cache = EvalCache("banking77_cache.json")
    if no_cache:
        cache.clear()

    results: list[dict] = []
    groq_calls = 0

    for case in cases:
        case_id = case["id"]
        cache_key = EvalCache.key(case_id, EVAL_MODEL, CLASSIFICATION_PROMPT_VERSION)

        if cache.has(cache_key):
            entry = cache.get(cache_key)
            predicted = entry.get("parsed", {})
            results.append({
                "id":                       case_id,
                "text":                     case["text"],
                "original_intent":          case["original_intent"],
                "gold_supportflow_category": case["gold_supportflow_category"],
                "mapping_ambiguous":        case["mapping_ambiguous"],
                "predicted_category":       predicted.get("category"),
                "from_cache":               True,
                "error":                    None,
            })
            print(f"  [b77] {case_id}: cached ✓")
            continue

        # Live Groq call
        print(f"  [b77] {case_id}: calling Groq ... ", end="", flush=True)
        predicted = None
        predicted_category = None
        error = None
        raw = {}
        
        try:
            predicted = classify_ticket(case["text"])
            raw = predicted
            predicted_category = predicted.get("category")
            print(f"OK (category={predicted_category})")
            groq_calls += 1
        except Exception as exc:
            error = str(exc)
            print(f"ERROR: {error}")
            groq_calls += 1

        # Persist immediately — even on error we record what happened.
        cache.put(cache_key, raw_response=raw, parsed=predicted or {})

        results.append({
            "id":                       case_id,
            "text":                     case["text"],
            "original_intent":          case["original_intent"],
            "gold_supportflow_category": case["gold_supportflow_category"],
            "mapping_ambiguous":        case["mapping_ambiguous"],
            "predicted_category":       predicted_category,
            "from_cache":               False,
            "error":                    error,
        })

        # Rate limit: pause between sequential calls.
        if groq_calls > 0:
            time.sleep(GROQ_CALL_DELAY_SECONDS)

    print(
        f"  [b77] done — {len(cases)} cases, "
        f"{groq_calls} Groq calls, "
        f"{len(cases) - groq_calls} from cache"
    )
    return results
