"""
Runner — Metric 5: Unauthorized-action blocking rate.

Calls authorize_and_get_order_status() directly from app.tools.
Zero Groq calls.  GROQ_API_KEY is not required.

Each dataset case specifies:
  - authenticated_customer_id  (the JWT-verified identity)
  - order_id                   (the resource being accessed)
  - order_owner_id             (who actually owns it; None = nonexistent order)
  - expected                   ALLOW | BLOCK | NOT_FOUND

The runner maps the backend's (authorized, error, result) triple to the
same three labels so the metric can compare expected vs actual directly.
"""

from __future__ import annotations

import json
import os

from evaluation.config import DATASETS_DIR, EVAL_DATABASE_URL


def _classify_outcome(authorized: bool, result: dict | None) -> str:
    """Maps authorize_and_get_order_status return to ALLOW / BLOCK / NOT_FOUND."""
    if not authorized:
        return "BLOCK"
    if result and "error" in result and "not found" in result["error"].lower():
        return "NOT_FOUND"
    return "ALLOW"


def run() -> list[dict]:
    """
    Evaluates authorization enforcement on the authorization dataset.

    Returns:
        List of result dicts:
            {
              "id":        str,
              "description": str,
              "authenticated_customer_id": int,
              "order_id":  int,
              "expected":  str,     # ALLOW | BLOCK | NOT_FOUND
              "actual":    str,     # ALLOW | BLOCK | NOT_FOUND
              "correct":   bool,
            }
    """
    os.environ["DATABASE_URL"] = EVAL_DATABASE_URL

    from app.tools import authorize_and_get_order_status

    dataset_path = DATASETS_DIR / "authorization.json"
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))

    results: list[dict] = []

    for case in dataset:
        case_id     = case["id"]
        cust_id     = case["authenticated_customer_id"]
        order_id    = case["order_id"]
        expected    = case["expected"]

        authorized, _err, result = authorize_and_get_order_status(
            authenticated_customer_id=cust_id,
            order_id=order_id,
        )

        actual  = _classify_outcome(authorized, result)
        correct = (actual == expected)

        mark = "✓" if correct else f"✗ (got {actual!r})"
        print(f"  [auth] {case_id}: expected={expected!r} actual={actual!r} {mark}")

        results.append({
            "id":                        case_id,
            "description":               case["description"],
            "authenticated_customer_id": cust_id,
            "order_id":                  order_id,
            "expected":                  expected,
            "actual":                    actual,
            "correct":                   correct,
        })

    correct_count = sum(1 for r in results if r["correct"])
    print(
        f"  [auth] done — {len(results)} cases, "
        f"{correct_count}/{len(results)} correct, 0 Groq calls"
    )
    return results
