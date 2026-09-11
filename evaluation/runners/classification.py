"""
Runner — Metric 1: Classification accuracy.

Calls classify_ticket() from app.llm sequentially.
Caches every result immediately keyed by case_id + model + prompt_version.
Skips cases already in cache (resume-safe).

Groq calls: up to 20 on a clean run, 0 on a fully-cached run.
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
    Evaluates the classifier on the classification dataset.

    Args:
        no_cache: If True, ignores and clears any existing cache.

    Returns:
        List of result dicts, one per dataset case:
            {
              "id":        str,
              "message":   str,
              "gold":      {"category", "priority", "sentiment"},
              "predicted": {"category", "priority", "sentiment"} | None,
              "from_cache": bool,
              "error":     str | None,
            }
    """
    # Guard: we need GROQ_API_KEY for this runner.
    if not os.getenv("GROQ_API_KEY"):
        raise EnvironmentError(
            "GROQ_API_KEY is not set. "
            "Classification runner requires a live Groq API key."
        )

    # Import here so DATABASE_URL is already set by the time app.llm loads.
    from app.llm import classify_ticket

    dataset_path = DATASETS_DIR / "classification.json"
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))

    cache = EvalCache("classification_cache.json")
    if no_cache:
        cache.clear()

    results: list[dict] = []
    groq_calls = 0

    for case in dataset:
        case_id = case["id"]
        cache_key = EvalCache.key(case_id, EVAL_MODEL, CLASSIFICATION_PROMPT_VERSION)

        if cache.has(cache_key):
            entry = cache.get(cache_key)
            results.append({
                "id":         case_id,
                "message":    case["message"],
                "gold":       case["gold"],
                "predicted":  entry["parsed"],
                "from_cache": True,
                "error":      None,
            })
            print(f"  [cls] {case_id}: cached ✓")
            continue

        # Live Groq call
        print(f"  [cls] {case_id}: calling Groq ... ", end="", flush=True)
        predicted = None
        error = None
        raw = {}
        try:
            predicted = classify_ticket(case["message"])
            raw = predicted
            print(f"OK (category={predicted.get('category')})")
            groq_calls += 1
        except Exception as exc:
            error = str(exc)
            print(f"ERROR: {error}")
            groq_calls += 1

        # Persist immediately — even on error we record what happened.
        cache.put(cache_key, raw_response=raw, parsed=predicted or {})

        results.append({
            "id":         case_id,
            "message":    case["message"],
            "gold":       case["gold"],
            "predicted":  predicted,
            "from_cache": False,
            "error":      error,
        })

        # Rate limit: pause between sequential calls.
        if groq_calls > 0:
            time.sleep(GROQ_CALL_DELAY_SECONDS)

    print(
        f"  [cls] done — {len(dataset)} cases, "
        f"{groq_calls} Groq calls, "
        f"{len(dataset) - groq_calls} from cache"
    )
    return results
