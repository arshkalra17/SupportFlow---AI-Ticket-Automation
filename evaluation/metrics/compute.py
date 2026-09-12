"""
Pure metric computation functions for the SupportFlow evaluation benchmark.

All functions here are stateless and free of I/O.
They accept lists of result dicts and return metric dicts.
No Groq calls, no database access.
"""

from __future__ import annotations
from typing import Any


# ══════════════════════════════════════════════════════════════════════
# Metric 1 — Classification accuracy
# ══════════════════════════════════════════════════════════════════════

def classification_accuracy(results: list[dict]) -> dict:
    """
    Computes per-field and exact-match accuracy for classification results.

    Each result dict must have:
        gold:      {"category": str, "priority": str, "sentiment": str}
        predicted: {"category": str, "priority": str, "sentiment": str}
                   OR None if the LLM call failed for this case.

    Comparison is case-insensitive.

    Returns:
        {
          "n": int,
          "n_evaluated": int,           # cases where predicted is not None
          "n_failed": int,              # cases where predicted is None (LLM error)
          "category_accuracy": float,
          "priority_accuracy": float,
          "sentiment_accuracy": float,
          "exact_match_accuracy": float,
          "category_correct": int,
          "priority_correct": int,
          "sentiment_correct": int,
          "exact_match_correct": int,
        }
    """
    n = len(results)
    evaluated = [r for r in results if r.get("predicted") is not None]
    n_evaluated = len(evaluated)
    n_failed = n - n_evaluated

    cat_correct = sum(
        1 for r in evaluated
        if (r["predicted"].get("category") or "").strip().lower()
        == (r["gold"].get("category") or "").strip().lower()
    )
    pri_correct = sum(
        1 for r in evaluated
        if (r["predicted"].get("priority") or "").strip().lower()
        == (r["gold"].get("priority") or "").strip().lower()
    )
    sen_correct = sum(
        1 for r in evaluated
        if (r["predicted"].get("sentiment") or "").strip().lower()
        == (r["gold"].get("sentiment") or "").strip().lower()
    )
    exact_correct = sum(
        1 for r in evaluated
        if (
            (r["predicted"].get("category") or "").strip().lower()
            == (r["gold"].get("category") or "").strip().lower()
            and (r["predicted"].get("priority") or "").strip().lower()
            == (r["gold"].get("priority") or "").strip().lower()
            and (r["predicted"].get("sentiment") or "").strip().lower()
            == (r["gold"].get("sentiment") or "").strip().lower()
        )
    )

    def _pct(correct: int, total: int) -> float:
        return round(correct / total, 4) if total > 0 else 0.0

    return {
        "n": n,
        "n_evaluated": n_evaluated,
        "n_failed": n_failed,
        "category_accuracy":   _pct(cat_correct,   n_evaluated),
        "priority_accuracy":   _pct(pri_correct,   n_evaluated),
        "sentiment_accuracy":  _pct(sen_correct,   n_evaluated),
        "exact_match_accuracy": _pct(exact_correct, n_evaluated),
        "category_correct":    cat_correct,
        "priority_correct":    pri_correct,
        "sentiment_correct":   sen_correct,
        "exact_match_correct": exact_correct,
    }


# ══════════════════════════════════════════════════════════════════════
# Metric 2 — Tool-selection accuracy
# ══════════════════════════════════════════════════════════════════════

def tool_selection_accuracy(results: list[dict]) -> dict:
    """
    Computes tool-sequence accuracy and first-tool accuracy.

    Each result dict must have:
        gold_tools:   list[str]  — expected ordered tool names
        actual_tools: list[str]  — tools actually selected by the LLM,
                                   in order of first call.
                                   None if LLM call failed.

    sequence_accuracy:
        Fraction of cases where actual_tools == gold_tools (exact ordered match).

    first_tool_accuracy:
        Among cases where gold_tools is non-empty, fraction where
        actual_tools[0] == gold_tools[0].

    Returns:
        {
          "n": int,
          "n_evaluated": int,
          "n_failed": int,
          "sequence_accuracy": float,
          "first_tool_accuracy": float,
          "sequence_correct": int,
          "first_tool_correct": int,
          "n_first_tool_eligible": int,   # cases with non-empty gold
        }
    """
    n = len(results)
    evaluated = [r for r in results if r.get("actual_tools") is not None]
    n_evaluated = len(evaluated)
    n_failed = n - n_evaluated

    seq_correct = sum(
        1 for r in evaluated
        if r["actual_tools"] == r["gold_tools"]
    )

    first_eligible = [
        r for r in evaluated if len(r["gold_tools"]) > 0
    ]
    first_correct = sum(
        1 for r in first_eligible
        if len(r["actual_tools"]) > 0
        and r["actual_tools"][0] == r["gold_tools"][0]
    )

    def _pct(correct: int, total: int) -> float:
        return round(correct / total, 4) if total > 0 else 0.0

    return {
        "n": n,
        "n_evaluated": n_evaluated,
        "n_failed": n_failed,
        "sequence_accuracy":        _pct(seq_correct,   n_evaluated),
        "first_tool_accuracy":      _pct(first_correct, len(first_eligible)),
        "sequence_correct":         seq_correct,
        "first_tool_correct":       first_correct,
        "n_first_tool_eligible":    len(first_eligible),
    }


# ══════════════════════════════════════════════════════════════════════
# Metric 3 & 4 — RAG Hit@1, Hit@3, out-of-domain rejection
# ══════════════════════════════════════════════════════════════════════

def rag_retrieval_accuracy(results: list[dict]) -> dict:
    """
    Computes Hit@1, Hit@3, and out-of-domain rejection rate.

    Each result dict must have:
        gold_title:     str or None   — expected top document title (None = out-of-domain)
        out_of_domain:  bool
        retrieved:      list[dict]    — returned by search_knowledge_base(),
                                        each with "title" key, ordered rank 1..k

    Hit@1:
        Among in-domain cases, fraction where retrieved[0].title == gold_title.

    Hit@3:
        Among in-domain cases, fraction where gold_title appears in top-3 titles.

    out_of_domain_rejection:
        Among out-of-domain cases, fraction where retrieved == []
        (i.e. similarity threshold filtered everything out).

    Returns:
        {
          "n_in_domain": int,
          "n_out_of_domain": int,
          "hit_at_1": float,
          "hit_at_3": float,
          "out_of_domain_rejection": float,
          "hit_at_1_correct": int,
          "hit_at_3_correct": int,
          "ood_rejected_correctly": int,
        }
    """
    in_domain  = [r for r in results if not r["out_of_domain"]]
    out_domain = [r for r in results if r["out_of_domain"]]

    hit1_correct = sum(
        1 for r in in_domain
        if r["retrieved"]
        and r["retrieved"][0]["title"] == r["gold_title"]
    )
    hit3_correct = sum(
        1 for r in in_domain
        if any(doc["title"] == r["gold_title"] for doc in r["retrieved"][:3])
    )
    ood_rejected = sum(
        1 for r in out_domain
        if len(r["retrieved"]) == 0
    )

    def _pct(correct: int, total: int) -> float:
        return round(correct / total, 4) if total > 0 else 0.0

    return {
        "n_in_domain":             len(in_domain),
        "n_out_of_domain":         len(out_domain),
        "hit_at_1":                _pct(hit1_correct, len(in_domain)),
        "hit_at_3":                _pct(hit3_correct, len(in_domain)),
        "out_of_domain_rejection": _pct(ood_rejected, len(out_domain)),
        "hit_at_1_correct":        hit1_correct,
        "hit_at_3_correct":        hit3_correct,
        "ood_rejected_correctly":  ood_rejected,
    }


# ══════════════════════════════════════════════════════════════════════
# Metric 5 — Unauthorized-action blocking rate
# ══════════════════════════════════════════════════════════════════════

def authorization_accuracy(results: list[dict]) -> dict:
    """
    Computes blocking rate and allow accuracy.

    Each result dict must have:
        expected: "ALLOW" | "BLOCK" | "NOT_FOUND"
        actual:   "ALLOW" | "BLOCK" | "NOT_FOUND"

    blocking_rate:
        Among BLOCK cases, fraction correctly returned BLOCK.

    allow_accuracy:
        Among ALLOW cases, fraction correctly returned ALLOW.

    overall_accuracy:
        Fraction of all cases with correct outcome.

    Returns:
        {
          "n": int,
          "n_block_cases": int,
          "n_allow_cases": int,
          "n_not_found_cases": int,
          "blocking_rate": float,
          "allow_accuracy": float,
          "overall_accuracy": float,
          "blocked_correctly": int,
          "allowed_correctly": int,
        }
    """
    n = len(results)
    block_cases    = [r for r in results if r["expected"] == "BLOCK"]
    allow_cases    = [r for r in results if r["expected"] == "ALLOW"]
    notfound_cases = [r for r in results if r["expected"] == "NOT_FOUND"]

    blocked_correctly = sum(1 for r in block_cases    if r["actual"] == "BLOCK")
    allowed_correctly = sum(1 for r in allow_cases    if r["actual"] == "ALLOW")
    notfound_correctly = sum(1 for r in notfound_cases if r["actual"] == "NOT_FOUND")

    overall_correct = blocked_correctly + allowed_correctly + notfound_correctly

    def _pct(correct: int, total: int) -> float:
        return round(correct / total, 4) if total > 0 else 0.0

    return {
        "n": n,
        "n_block_cases":    len(block_cases),
        "n_allow_cases":    len(allow_cases),
        "n_not_found_cases": len(notfound_cases),
        "blocking_rate":    _pct(blocked_correctly, len(block_cases)),
        "allow_accuracy":   _pct(allowed_correctly, len(allow_cases)),
        "overall_accuracy": _pct(overall_correct,   n),
        "blocked_correctly": blocked_correctly,
        "allowed_correctly": allowed_correctly,
    }


# ══════════════════════════════════════════════════════════════════════
# Metric 6 — Approval-routing accuracy
# ══════════════════════════════════════════════════════════════════════

def approval_routing_accuracy(results: list[dict]) -> dict:
    """
    Computes approval-routing accuracy.

    Each result dict must have:
        expected: "AUTO_EXECUTE" | "HUMAN_APPROVAL" | "REJECT"
        actual:   "AUTO_EXECUTE" | "HUMAN_APPROVAL" | "REJECT"

    Returns:
        {
          "n": int,
          "routing_accuracy": float,
          "correct": int,
          "by_expected": {
              "AUTO_EXECUTE":   {"n": int, "correct": int, "accuracy": float},
              "HUMAN_APPROVAL": {"n": int, "correct": int, "accuracy": float},
              "REJECT":         {"n": int, "correct": int, "accuracy": float},
          }
        }
    """
    n = len(results)
    correct = sum(1 for r in results if r["actual"] == r["expected"])

    by_expected: dict[str, dict] = {}
    for label in ("AUTO_EXECUTE", "HUMAN_APPROVAL", "REJECT"):
        subset = [r for r in results if r["expected"] == label]
        sub_correct = sum(1 for r in subset if r["actual"] == label)
        by_expected[label] = {
            "n":        len(subset),
            "correct":  sub_correct,
            "accuracy": round(sub_correct / len(subset), 4) if subset else 0.0,
        }

    def _pct(c: int, t: int) -> float:
        return round(c / t, 4) if t > 0 else 0.0

    return {
        "n":               n,
        "routing_accuracy": _pct(correct, n),
        "correct":         correct,
        "by_expected":     by_expected,
    }


# ══════════════════════════════════════════════════════════════════════
# BANKING77 External Benchmark — Category-only accuracy
# ══════════════════════════════════════════════════════════════════════

def banking77_category_accuracy(results: list[dict]) -> dict:
    """
    Computes category-only accuracy for BANKING77 external benchmark.

    This is a CROSS-DOMAIN EXTERNAL CATEGORY BENCHMARK.
    It measures ONLY category classification.

    Each result dict must have:
        original_intent:          str         — BANKING77 intent
        gold_supportflow_category: str | None — mapped SupportFlow category
        mapping_ambiguous:        bool        — whether mapping is ambiguous
        predicted_category:       str | None  — predicted SupportFlow category
        error:                    str | None  — LLM error if any

    Ambiguous mappings are excluded from the primary accuracy metric.

    Returns:
        {
          "n_total": int,
          "n_mapped": int,              # non-ambiguous cases
          "n_ambiguous": int,
          "n_evaluated": int,           # non-ambiguous cases with prediction
          "n_failed": int,              # non-ambiguous cases with error
          "mapped_category_accuracy": float,
          "category_correct": int,
          "by_supportflow_category": {  # per-category breakdown
              "Technical Support": {"n": int, "correct": int, "accuracy": float},
              ...
          },
          "by_original_intent": {       # per-intent breakdown
              "activate_my_card": {"n": int, "correct": int, "accuracy": float},
              ...
          },
          "confusion_matrix": {         # predicted → gold
              "Technical Support": {
                  "Technical Support": int,
                  "Billing": int,
                  ...
              },
              ...
          }
        }
    """
    n_total = len(results)
    
    # Filter to non-ambiguous mappings only
    mapped = [r for r in results if not r.get("mapping_ambiguous", False)]
    n_mapped = len(mapped)
    n_ambiguous = n_total - n_mapped
    
    # Filter to evaluated (non-ambiguous with prediction)
    evaluated = [r for r in mapped if r.get("predicted_category") is not None and r.get("error") is None]
    n_evaluated = len(evaluated)
    n_failed = n_mapped - n_evaluated
    
    # Primary metric: category accuracy on non-ambiguous cases
    category_correct = sum(
        1 for r in evaluated
        if (r.get("predicted_category") or "").strip().lower()
        == (r.get("gold_supportflow_category") or "").strip().lower()
    )
    
    def _pct(correct: int, total: int) -> float:
        return round(correct / total, 4) if total > 0 else 0.0
    
    # Per-category breakdown
    categories = ["Technical Support", "Billing", "Order Issue", "General Inquiry", "Refund Request"]
    by_category: dict[str, dict] = {}
    
    for cat in categories:
        cat_cases = [r for r in evaluated if r.get("gold_supportflow_category") == cat]
        cat_correct = sum(
            1 for r in cat_cases
            if r.get("predicted_category") == cat
        )
        by_category[cat] = {
            "n": len(cat_cases),
            "correct": cat_correct,
            "accuracy": _pct(cat_correct, len(cat_cases))
        }
    
    # Per-intent breakdown
    from collections import defaultdict
    by_intent: dict[str, dict] = {}
    intent_groups = defaultdict(list)
    
    for r in evaluated:
        intent_groups[r["original_intent"]].append(r)
    
    for intent, cases in intent_groups.items():
        intent_correct = sum(
            1 for r in cases
            if (r.get("predicted_category") or "").strip().lower()
            == (r.get("gold_supportflow_category") or "").strip().lower()
        )
        by_intent[intent] = {
            "n": len(cases),
            "correct": intent_correct,
            "accuracy": _pct(intent_correct, len(cases))
        }
    
    # Confusion matrix
    confusion: dict[str, dict[str, int]] = {}
    for pred_cat in categories + ["Other"]:
        confusion[pred_cat] = {gold_cat: 0 for gold_cat in categories}
    
    for r in evaluated:
        pred = r.get("predicted_category") or "Other"
        gold = r.get("gold_supportflow_category")
        
        if pred not in categories:
            pred = "Other"
        if gold in categories:
            confusion[pred][gold] += 1
    
    return {
        "n_total": n_total,
        "n_mapped": n_mapped,
        "n_ambiguous": n_ambiguous,
        "n_evaluated": n_evaluated,
        "n_failed": n_failed,
        "mapped_category_accuracy": _pct(category_correct, n_evaluated),
        "category_correct": category_correct,
        "by_supportflow_category": by_category,
        "by_original_intent": by_intent,
        "confusion_matrix": confusion,
    }
