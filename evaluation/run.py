"""
SupportFlow Stage 9 — AI Evaluation Benchmark entry point.

Usage
─────
Run all metrics (deterministic first, LLM second):
    python -m evaluation.run

Run a single metric:
    python -m evaluation.run --metric classification
    python -m evaluation.run --metric tools
    python -m evaluation.run --metric rag
    python -m evaluation.run --metric authorization
    python -m evaluation.run --metric approval

Skip LLM metrics even when GROQ_API_KEY is set:
    python -m evaluation.run --no-llm

Ignore existing cache and re-run all LLM cases:
    python -m evaluation.run --no-cache

Outputs
───────
evaluation/results/latest.json  — machine-readable full results
evaluation/results/latest.md    — human-readable summary
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
groq_api_key = os.getenv("GROQ_API_KEY")
# ── Make sure the project root is on sys.path when invoked as a module ─
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from evaluation.config import (
    EVAL_DATABASE_URL,
    EVAL_MODEL,
    CLASSIFICATION_PROMPT_VERSION,
    TOOL_SELECTION_PROMPT_VERSION,
    RESULTS_DIR,
)
from evaluation.metrics.compute import (
    classification_accuracy,
    tool_selection_accuracy,
    rag_retrieval_accuracy,
    authorization_accuracy,
    approval_routing_accuracy,
)


# ══════════════════════════════════════════════════════════════════════
# Result output
# ══════════════════════════════════════════════════════════════════════

def _pct(value: float) -> str:
    """Format a 0–1 fraction as a percentage string."""
    return f"{value * 100:.1f}%"


def write_results(all_metrics: dict, groq_calls: dict) -> None:
    """Writes latest.json and latest.md to evaluation/results/."""

    timestamp = datetime.now(timezone.utc).isoformat()

    # ── JSON ──────────────────────────────────────────────────────────
    output = {
        "run_timestamp":     timestamp,
        "model":             EVAL_MODEL,
        "classification_prompt_version": CLASSIFICATION_PROMPT_VERSION,
        "tool_selection_prompt_version": TOOL_SELECTION_PROMPT_VERSION,
        "metrics":           all_metrics,
        "llm_calls_summary": {
            **groq_calls,
            "total": sum(groq_calls.values()),
        },
    }

    json_path = RESULTS_DIR / "latest.json"
    json_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  [output] Written: {json_path}")

    # ── Markdown ──────────────────────────────────────────────────────
    lines: list[str] = []
    lines.append("# SupportFlow AI Evaluation")
    lines.append("")
    lines.append(f"Run timestamp : {timestamp}")
    lines.append(f"Model         : {EVAL_MODEL}")
    lines.append("")
    lines.append("```")
    lines.append("=" * 48)
    lines.append("SupportFlow AI Evaluation")
    lines.append("=" * 48)

    cls = all_metrics.get("classification")
    if cls:
        lines.append("")
        lines.append("Classification")
        lines.append(f"  Dataset size        : {cls['n']} cases")
        lines.append(f"  Evaluated           : {cls['n_evaluated']}"
                     + (f"  (failed={cls['n_failed']})" if cls['n_failed'] else ""))
        lines.append(f"  Category accuracy   : {_pct(cls['category_accuracy'])}")
        lines.append(f"  Priority accuracy   : {_pct(cls['priority_accuracy'])}")
        lines.append(f"  Sentiment accuracy  : {_pct(cls['sentiment_accuracy'])}")
        lines.append(f"  Exact-match accuracy: {_pct(cls['exact_match_accuracy'])}")

    tools = all_metrics.get("tool_selection")
    if tools:
        lines.append("")
        lines.append("Tool Selection")
        lines.append(f"  Dataset size        : {tools['n']} cases")
        lines.append(f"  Evaluated           : {tools['n_evaluated']}"
                     + (f"  (failed={tools['n_failed']})" if tools['n_failed'] else ""))
        lines.append(f"  Sequence accuracy   : {_pct(tools['sequence_accuracy'])}")
        lines.append(f"  First-tool accuracy : {_pct(tools['first_tool_accuracy'])}")

    rag = all_metrics.get("rag")
    if rag:
        lines.append("")
        lines.append("RAG Retrieval")
        lines.append(f"  In-domain cases     : {rag['n_in_domain']}")
        lines.append(f"  Out-of-domain cases : {rag['n_out_of_domain']}")
        lines.append(f"  Hit@1               : {_pct(rag['hit_at_1'])}")
        lines.append(f"  Hit@3               : {_pct(rag['hit_at_3'])}")
        lines.append(f"  OOD rejection rate  : {_pct(rag['out_of_domain_rejection'])}")

    auth = all_metrics.get("authorization")
    if auth:
        lines.append("")
        lines.append("Security (Authorization)")
        lines.append(f"  Dataset size        : {auth['n']} cases")
        lines.append(f"  BLOCK cases         : {auth['n_block_cases']}")
        lines.append(f"  ALLOW cases         : {auth['n_allow_cases']}")
        lines.append(f"  Blocking rate       : {_pct(auth['blocking_rate'])}")
        lines.append(f"  Allow accuracy      : {_pct(auth['allow_accuracy'])}")
        lines.append(f"  Overall accuracy    : {_pct(auth['overall_accuracy'])}")

    appr = all_metrics.get("approval_routing")
    if appr:
        lines.append("")
        lines.append("Approval Routing")
        lines.append(f"  Dataset size        : {appr['n']} cases")
        lines.append(f"  Routing accuracy    : {_pct(appr['routing_accuracy'])}")
        for label, sub in appr["by_expected"].items():
            if sub["n"] > 0:
                lines.append(
                    f"    {label:<18}: {sub['correct']}/{sub['n']} "
                    f"({_pct(sub['accuracy'])})"
                )

    lines.append("")
    lines.append("LLM calls used")
    for metric, n in groq_calls.items():
        lines.append(f"  {metric:<22}: {n}")
    lines.append(f"  {'total':<22}: {sum(groq_calls.values())}")

    lines.append("=" * 48)
    lines.append("```")

    md_path = RESULTS_DIR / "latest.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  [output] Written: {md_path}")


# ══════════════════════════════════════════════════════════════════════
# Individual metric runners
# ══════════════════════════════════════════════════════════════════════

def run_classification(no_cache: bool) -> tuple[dict, int]:
    from evaluation.runners.classification import run
    print("\n[Metric 1] Classification accuracy")
    results = run(no_cache=no_cache)
    metrics = classification_accuracy(results)
    calls   = sum(1 for r in results if not r["from_cache"] and r["error"] is None)
    calls  += sum(1 for r in results if not r["from_cache"] and r["error"] is not None)
    return metrics, calls


def run_tool_selection(no_cache: bool) -> tuple[dict, int]:
    from evaluation.runners.tool_selection import run
    print("\n[Metric 2] Tool-selection accuracy")
    results = run(no_cache=no_cache)
    metrics = tool_selection_accuracy(results)
    calls   = sum(1 for r in results if not r["from_cache"])
    return metrics, calls


def run_rag() -> dict:
    from evaluation.runners.rag import run
    print("\n[Metric 3 & 4] RAG Hit@1 / Hit@3 / OOD rejection")
    results = run()
    return rag_retrieval_accuracy(results)


def run_authorization() -> dict:
    from evaluation.runners.authorization import run
    print("\n[Metric 5] Unauthorized-action blocking rate")
    results = run()
    return authorization_accuracy(results)


def run_approval() -> dict:
    from evaluation.runners.approval import run
    print("\n[Metric 6] Approval-routing accuracy")
    results = run()
    return approval_routing_accuracy(results)


# ══════════════════════════════════════════════════════════════════════
# DB lifecycle
# ══════════════════════════════════════════════════════════════════════

def setup_db() -> object:
    """Sets up the evaluation database and returns the session."""
    os.environ["DATABASE_URL"] = EVAL_DATABASE_URL
    print("\n[DB] Setting up evaluation database (supportflow_eval) ...")
    from evaluation.db_setup import setup_eval_db
    return setup_eval_db()


def teardown_db(session: object) -> None:
    print("\n[DB] Cleaning up evaluation database ...")
    from evaluation.db_setup import teardown_eval_db
    teardown_eval_db(session)


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

VALID_METRICS = {"classification", "tools", "rag", "authorization", "approval"}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SupportFlow Stage 9 — AI Evaluation Benchmark",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python -m evaluation.run\n"
            "  python -m evaluation.run --metric rag\n"
            "  python -m evaluation.run --no-llm\n"
            "  python -m evaluation.run --metric classification --no-cache\n"
        ),
    )
    parser.add_argument(
        "--metric",
        choices=list(VALID_METRICS),
        default=None,
        help="Run only one metric (default: all).",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip metrics that require Groq (classification, tools).",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Ignore existing cache; re-run all LLM cases.",
    )
    args = parser.parse_args()

    want_cls   = (args.metric in (None, "classification")) and not args.no_llm
    want_tools = (args.metric in (None, "tools"))          and not args.no_llm
    want_rag   = (args.metric in (None, "rag"))
    want_auth  = (args.metric in (None, "authorization"))
    want_appr  = (args.metric in (None, "approval"))

    # LLM metrics require GROQ_API_KEY.
    groq_key = os.getenv("GROQ_API_KEY")
    if (want_cls or want_tools) and not groq_key:
        print(
            "\n[WARNING] GROQ_API_KEY is not set.\n"
            "  LLM-dependent metrics (classification, tools) will be skipped.\n"
            "  Set GROQ_API_KEY or use --no-llm to suppress this warning.\n"
        )
        want_cls   = False
        want_tools = False

    print("=" * 56)
    print("  SupportFlow AI Evaluation Benchmark — Stage 9")
    print("=" * 56)
    print(f"  Model            : {EVAL_MODEL}")
    print(f"  Eval database    : {EVAL_DATABASE_URL}")
    print(f"  Metrics requested: {args.metric or 'all'}")
    print(f"  LLM metrics      : {'skip (--no-llm or no key)' if not (want_cls or want_tools) else 'yes'}")
    print(f"  Cache            : {'disabled (--no-cache)' if args.no_cache else 'enabled'}")
    print("=" * 56)

    # ── Set up eval database ─────────────────────────────────────────
    db_session = setup_db()

    all_metrics: dict  = {}
    groq_calls: dict   = {
        "classification": 0,
        "tool_selection":  0,
        "rag":             0,
        "authorization":   0,
        "approval_routing": 0,
    }

    try:
        # ── Deterministic metrics (no Groq) ───────────────────────────
        if want_rag:
            all_metrics["rag"] = run_rag()

        if want_auth:
            all_metrics["authorization"] = run_authorization()

        if want_appr:
            all_metrics["approval_routing"] = run_approval()

        # ── LLM-dependent metrics ─────────────────────────────────────
        if want_cls:
            m, calls = run_classification(no_cache=args.no_cache)
            all_metrics["classification"] = m
            groq_calls["classification"]  = calls

        if want_tools:
            m, calls = run_tool_selection(no_cache=args.no_cache)
            all_metrics["tool_selection"] = m
            groq_calls["tool_selection"]  = calls

    finally:
        # ── Always clean up eval DB ───────────────────────────────────
        teardown_db(db_session)

    if not all_metrics:
        print("\nNo metrics were run.")
        return

    # ── Print summary to console ──────────────────────────────────────
    print("\n" + "=" * 56)
    print("  RESULTS SUMMARY")
    print("=" * 56)

    if "classification" in all_metrics:
        cls = all_metrics["classification"]
        print(f"\nClassification  (n={cls['n']}, evaluated={cls['n_evaluated']})")
        print(f"  Category     : {_pct(cls['category_accuracy'])}")
        print(f"  Priority     : {_pct(cls['priority_accuracy'])}")
        print(f"  Sentiment    : {_pct(cls['sentiment_accuracy'])}")
        print(f"  Exact-match  : {_pct(cls['exact_match_accuracy'])}")

    if "tool_selection" in all_metrics:
        t = all_metrics["tool_selection"]
        print(f"\nTool Selection  (n={t['n']}, evaluated={t['n_evaluated']})")
        print(f"  Sequence     : {_pct(t['sequence_accuracy'])}")
        print(f"  First-tool   : {_pct(t['first_tool_accuracy'])}")

    if "rag" in all_metrics:
        r = all_metrics["rag"]
        print(f"\nRAG Retrieval  (in-domain={r['n_in_domain']}, ood={r['n_out_of_domain']})")
        print(f"  Hit@1        : {_pct(r['hit_at_1'])}")
        print(f"  Hit@3        : {_pct(r['hit_at_3'])}")
        print(f"  OOD reject   : {_pct(r['out_of_domain_rejection'])}")

    if "authorization" in all_metrics:
        a = all_metrics["authorization"]
        print(f"\nAuthorization  (n={a['n']}, BLOCK={a['n_block_cases']}, ALLOW={a['n_allow_cases']})")
        print(f"  Blocking     : {_pct(a['blocking_rate'])}")
        print(f"  Allow        : {_pct(a['allow_accuracy'])}")
        print(f"  Overall      : {_pct(a['overall_accuracy'])}")

    if "approval_routing" in all_metrics:
        ap = all_metrics["approval_routing"]
        print(f"\nApproval Routing  (n={ap['n']})")
        print(f"  Routing      : {_pct(ap['routing_accuracy'])}")

    total_calls = sum(groq_calls.values())
    print(f"\nTotal Groq calls: {total_calls}")
    print("=" * 56)

    write_results(all_metrics, groq_calls)


if __name__ == "__main__":
    main()
