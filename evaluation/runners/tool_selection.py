"""
Runner — Metric 2: Tool-selection accuracy.

Sends each message to Groq with the full tool schema and records which
tools the LLM requests, in order of first call.

Uses process_customer_message_with_tools() from app.llm so the same
system prompt and tool schemas are exercised as in production.

Groq calls: up to 15 on a clean run, 0 on a fully-cached run.

The runner uses evaluation order IDs that exist in supportflow_eval.
Messages reference order IDs 5001–5003 which are seeded by db_setup.
authenticated_customer_id=1 is used for all tool-selection cases so
the backend authorization layer passes and tool calls can execute.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from evaluation.cache import EvalCache
from evaluation.config import (
    DATASETS_DIR,
    EVAL_DATABASE_URL,
    EVAL_MODEL,
    TOOL_SELECTION_PROMPT_VERSION,
    GROQ_CALL_DELAY_SECONDS,
)


def _extract_tool_sequence(tool_call_log: list[dict]) -> list[str]:
    """
    Extracts the ordered list of unique tool names from the tool call log,
    preserving the order they were first requested.

    For multi-step flows (e.g. get_order_status → create_replacement_request)
    the full sequence is returned, including repetitions if a tool is called
    more than once.
    """
    return [tc["tool_name"] for tc in tool_call_log]


def run(no_cache: bool = False) -> list[dict]:
    """
    Evaluates tool selection on the tool_selection dataset.

    Returns:
        List of result dicts:
            {
              "id":           str,
              "message":      str,
              "gold_tools":   list[str],
              "actual_tools": list[str] | None,
              "from_cache":   bool,
              "error":        str | None,
            }
    """
    if not os.getenv("GROQ_API_KEY"):
        raise EnvironmentError(
            "GROQ_API_KEY is not set. "
            "Tool-selection runner requires a live Groq API key."
        )

    # Point the app at the evaluation database before importing app modules.
    os.environ["DATABASE_URL"] = EVAL_DATABASE_URL

    from app.llm import process_customer_message_with_tools

    dataset_path = DATASETS_DIR / "tool_selection.json"
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))

    cache = EvalCache("tool_selection_cache.json")
    if no_cache:
        cache.clear()

    results: list[dict] = []
    groq_calls = 0

    for case in dataset:
        case_id = case["id"]
        cache_key = EvalCache.key(case_id, EVAL_MODEL, TOOL_SELECTION_PROMPT_VERSION)

        if cache.has(cache_key):
            entry = cache.get(cache_key)
            results.append({
                "id":           case_id,
                "message":      case["message"],
                "gold_tools":   case["gold_tools"],
                "actual_tools": entry["parsed"].get("tools"),
                "from_cache":   True,
                "error":        None,
            })
            print(f"  [tool] {case_id}: cached ✓")
            continue

        print(f"  [tool] {case_id}: calling Groq ... ", end="", flush=True)
        actual_tools = None
        error = None
        raw: dict = {}

        try:
            response = process_customer_message_with_tools(
                message=case["message"],
                authenticated_customer_id=1,   # owner of orders 5001–5004
            )
            actual_tools = _extract_tool_sequence(response.get("tool_calls", []))
            raw = {
                "tool_calls":   response.get("tool_calls", []),
                "final_response": response.get("final_response", ""),
            }
            parsed = {"tools": actual_tools}
            print(f"OK (tools={actual_tools})")
            groq_calls += 1
        except Exception as exc:
            error = str(exc)
            parsed = {"tools": None}
            print(f"ERROR: {error}")
            groq_calls += 1

        cache.put(cache_key, raw_response=raw, parsed=parsed)

        results.append({
            "id":           case_id,
            "message":      case["message"],
            "gold_tools":   case["gold_tools"],
            "actual_tools": actual_tools,
            "from_cache":   False,
            "error":        error,
        })

        if groq_calls > 0:
            time.sleep(GROQ_CALL_DELAY_SECONDS)

    print(
        f"  [tool] done — {len(dataset)} cases, "
        f"{groq_calls} Groq calls, "
        f"{len(dataset) - groq_calls} from cache"
    )
    return results
