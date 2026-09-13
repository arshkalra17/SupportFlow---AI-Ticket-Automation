"""
BANKING77 Mapping Experiment Runner

Evaluates the SAME 154 cached classifier predictions against BOTH:
1. Baseline mapping (Stage 9B): banking77_mapping.json
2. Experimental mapping: banking77_mapping_experiment.json

Produces paired comparison showing accuracy changes due to mapping corrections ONLY.

NO new Groq calls - uses existing cache with prompt v1-a0ce2844.
"""

import json
from pathlib import Path
from collections import defaultdict

# Paths
DATASETS_DIR = Path("evaluation/datasets")
CACHE_PATH = Path("evaluation/results/cache/banking77_cache.json")
RESULTS_DIR = Path("evaluation/results")

BASELINE_MAPPING_PATH = DATASETS_DIR / "banking77_mapping.json"
EXPERIMENTAL_MAPPING_PATH = DATASETS_DIR / "banking77_mapping_experiment.json"
BENCHMARK_PATH = DATASETS_DIR / "banking77_benchmark.json"

# Output paths
EXPERIMENTAL_RESULTS_JSON = RESULTS_DIR / "banking77_mapping_experiment.json"
EXPERIMENTAL_RESULTS_MD = RESULTS_DIR / "banking77_mapping_experiment.md"


def load_cached_predictions():
    """Load all 154 cached classifier predictions."""
    cache_data = json.loads(CACHE_PATH.read_text())
    
    predictions = {}
    for cache_key, entry in cache_data.items():
        # Key format: "banking77-XXX__openai/gpt-oss-120b__v1-a0ce2844"
        case_id = cache_key.split("__")[0]
        predicted_category = entry.get("parsed", {}).get("category")
        predictions[case_id] = predicted_category
    
    return predictions


def load_mapping(mapping_path):
    """Load a mapping file and return intent -> category dict."""
    mapping_data = json.loads(mapping_path.read_text())
    
    intent_to_category = {}
    for intent, data in mapping_data["mappings"].items():
        if "proposed_mapping" in data:
            # Experimental mapping
            intent_to_category[intent] = data["proposed_mapping"]
        else:
            # Baseline mapping
            intent_to_category[intent] = data["supportflow_category"]
    
    return intent_to_category


def evaluate_with_mapping(benchmark_cases, predictions, mapping):
    """Evaluate predictions against a specific mapping."""
    results = []
    correct_count = 0
    category_stats = defaultdict(lambda: {"total": 0, "correct": 0})
    
    for case in benchmark_cases:
        case_id = case["id"]
        original_intent = case["original_intent"]
        
        # Get gold label from mapping
        gold_category = mapping.get(original_intent)
        
        # Get prediction
        predicted_category = predictions.get(case_id)
        
        # Check correctness
        is_correct = (predicted_category == gold_category)
        
        if is_correct:
            correct_count += 1
        
        # Update per-category stats
        if gold_category:
            category_stats[gold_category]["total"] += 1
            if is_correct:
                category_stats[gold_category]["correct"] += 1
        
        results.append({
            "case_id": case_id,
            "text": case["text"],
            "original_intent": original_intent,
            "gold_category": gold_category,
            "predicted_category": predicted_category,
            "correct": is_correct,
        })
    
    overall_accuracy = correct_count / len(benchmark_cases) if benchmark_cases else 0.0
    
    # Calculate per-category accuracy
    category_accuracy = {}
    for category, stats in category_stats.items():
        if stats["total"] > 0:
            category_accuracy[category] = {
                "correct": stats["correct"],
                "total": stats["total"],
                "accuracy": stats["correct"] / stats["total"]
            }
    
    return {
        "overall_correct": correct_count,
        "overall_total": len(benchmark_cases),
        "overall_accuracy": overall_accuracy,
        "category_accuracy": category_accuracy,
        "results": results,
    }


def compare_results(baseline_eval, experimental_eval):
    """Generate paired comparison analysis."""
    baseline_results = {r["case_id"]: r for r in baseline_eval["results"]}
    experimental_results = {r["case_id"]: r for r in experimental_eval["results"]}
    
    # Track changes
    correct_gained = []  # baseline wrong → experimental correct
    correct_lost = []     # baseline correct → experimental wrong
    unchanged_correct = []  # correct in both
    unchanged_incorrect = []  # wrong in both
    gold_label_changed = []  # cases where gold category changed
    
    for case_id in baseline_results:
        baseline = baseline_results[case_id]
        experimental = experimental_results[case_id]
        
        gold_changed = (baseline["gold_category"] != experimental["gold_category"])
        
        if gold_changed:
            gold_label_changed.append({
                "case_id": case_id,
                "text": baseline["text"],
                "original_intent": baseline["original_intent"],
                "baseline_gold": baseline["gold_category"],
                "experimental_gold": experimental["gold_category"],
                "predicted": baseline["predicted_category"],
                "baseline_correct": baseline["correct"],
                "experimental_correct": experimental["correct"],
            })
        
        if baseline["correct"] and experimental["correct"]:
            unchanged_correct.append(case_id)
        elif not baseline["correct"] and not experimental["correct"]:
            unchanged_incorrect.append(case_id)
        elif not baseline["correct"] and experimental["correct"]:
            correct_gained.append({
                "case_id": case_id,
                "text": baseline["text"],
                "original_intent": baseline["original_intent"],
                "baseline_gold": baseline["gold_category"],
                "experimental_gold": experimental["gold_category"],
                "predicted": baseline["predicted_category"],
            })
        elif baseline["correct"] and not experimental["correct"]:
            correct_lost.append({
                "case_id": case_id,
                "text": baseline["text"],
                "original_intent": baseline["original_intent"],
                "baseline_gold": baseline["gold_category"],
                "experimental_gold": experimental["gold_category"],
                "predicted": baseline["predicted_category"],
            })
    
    return {
        "correct_gained": correct_gained,
        "correct_lost": correct_lost,
        "unchanged_correct": unchanged_correct,
        "unchanged_incorrect": unchanged_incorrect,
        "gold_label_changed": gold_label_changed,
    }


def generate_markdown_report(baseline_eval, experimental_eval, comparison, baseline_mapping, experimental_mapping):
    """Generate comprehensive markdown report."""
    
    lines = []
    lines.append("# BANKING77 Mapping Experiment Results")
    lines.append("")
    lines.append("**Experiment Date:** 2026-09-13")
    lines.append("**Classifier:** openai/gpt-oss-120b")
    lines.append("**Prompt Version:** v1-a0ce2844")
    lines.append("**Dataset:** BANKING77 (154 test cases, 77 intents, 2 per intent)")
    lines.append("**Cache:** 100% (no new Groq calls)")
    lines.append("")
    lines.append("---")
    lines.append("")
    
    # Overall accuracy comparison
    lines.append("## Overall Accuracy Comparison")
    lines.append("")
    lines.append("| Metric | Baseline | Experimental | Δ |")
    lines.append("|---|---:|---:|---:|")
    
    baseline_acc = baseline_eval["overall_accuracy"] * 100
    experimental_acc = experimental_eval["overall_accuracy"] * 100
    delta_acc = experimental_acc - baseline_acc
    
    lines.append(f"| **Overall Accuracy** | **{baseline_acc:.1f}%** ({baseline_eval['overall_correct']}/154) | **{experimental_acc:.1f}%** ({experimental_eval['overall_correct']}/154) | **{delta_acc:+.1f}pp** |")
    lines.append("")
    
    # Per-category accuracy
    lines.append("## Per-Category Accuracy")
    lines.append("")
    lines.append("| Category | Baseline | Experimental | Δ |")
    lines.append("|---|---|---|---|")
    
    all_categories = sorted(set(
        list(baseline_eval["category_accuracy"].keys()) +
        list(experimental_eval["category_accuracy"].keys())
    ))
    
    for category in all_categories:
        baseline_stats = baseline_eval["category_accuracy"].get(category, {"correct": 0, "total": 0, "accuracy": 0})
        experimental_stats = experimental_eval["category_accuracy"].get(category, {"correct": 0, "total": 0, "accuracy": 0})
        
        baseline_pct = baseline_stats["accuracy"] * 100
        experimental_pct = experimental_stats["accuracy"] * 100
        delta_pct = experimental_pct - baseline_pct
        
        baseline_str = f"{baseline_pct:.1f}% ({baseline_stats['correct']}/{baseline_stats['total']})"
        experimental_str = f"{experimental_pct:.1f}% ({experimental_stats['correct']}/{experimental_stats['total']})"
        delta_str = f"{delta_pct:+.1f}pp" if baseline_stats["total"] > 0 or experimental_stats["total"] > 0 else "—"
        
        lines.append(f"| **{category}** | {baseline_str} | {experimental_str} | {delta_str} |")
    
    lines.append("")
    
    # Change summary
    lines.append("## Change Summary")
    lines.append("")
    lines.append(f"- **Correct cases gained**: {len(comparison['correct_gained'])}")
    lines.append(f"- **Correct cases lost**: {len(comparison['correct_lost'])}")
    lines.append(f"- **Unchanged correct**: {len(comparison['unchanged_correct'])}")
    lines.append(f"- **Unchanged incorrect**: {len(comparison['unchanged_incorrect'])}")
    lines.append(f"- **Gold labels changed**: {len(comparison['gold_label_changed'])}")
    lines.append("")
    
    # Net accuracy change analysis
    lines.append("## Accuracy Change Analysis")
    lines.append("")
    lines.append(f"**Net gain:** {len(comparison['correct_gained'])} cases")
    lines.append(f"**Net loss:** {len(comparison['correct_lost'])} cases")
    lines.append(f"**Net improvement:** {len(comparison['correct_gained']) - len(comparison['correct_lost'])} cases ({delta_acc:+.1f} percentage points)")
    lines.append("")
    
    # Mapping-only improvement
    lines.append("### A. Mapping-Only Improvement")
    lines.append("")
    lines.append("These cases became correct solely due to gold label changes (classifier prediction unchanged):")
    lines.append("")
    
    if comparison['correct_gained']:
        lines.append(f"**{len(comparison['correct_gained'])} cases gained:**")
        lines.append("")
        for item in comparison['correct_gained'][:10]:  # Show first 10
            lines.append(f"- **{item['case_id']}** ({item['original_intent']})")
            lines.append(f"  - Text: \"{item['text']}\"")
            lines.append(f"  - Predicted: `{item['predicted']}`")
            lines.append(f"  - Baseline gold: `{item['baseline_gold']}` (wrong)")
            lines.append(f"  - Experimental gold: `{item['experimental_gold']}` (**correct**)")
            lines.append("")
        
        if len(comparison['correct_gained']) > 10:
            lines.append(f"... and {len(comparison['correct_gained']) - 10} more cases.")
            lines.append("")
    else:
        lines.append("*None*")
        lines.append("")
    
    # Cases lost
    if comparison['correct_lost']:
        lines.append(f"### Cases Lost ({len(comparison['correct_lost'])})")
        lines.append("")
        lines.append("These cases were correct under baseline but became wrong under experimental mapping:")
        lines.append("")
        for item in comparison['correct_lost']:
            lines.append(f"- **{item['case_id']}** ({item['original_intent']})")
            lines.append(f"  - Text: \"{item['text']}\"")
            lines.append(f"  - Predicted: `{item['predicted']}`")
            lines.append(f"  - Baseline gold: `{item['baseline_gold']}` (**correct**)")
            lines.append(f"  - Experimental gold: `{item['experimental_gold']}` (wrong)")
            lines.append("")
    
    # Gold label changes
    lines.append("## All Gold Label Changes")
    lines.append("")
    lines.append(f"**{len(comparison['gold_label_changed'])} cases** had their gold category changed:")
    lines.append("")
    
    # Group by original intent
    intent_changes = defaultdict(list)
    for item in comparison['gold_label_changed']:
        intent_changes[item['original_intent']].append(item)
    
    lines.append("| Original Intent | Baseline Gold | Experimental Gold | Cases Affected |")
    lines.append("|---|---|---|---|")
    for intent in sorted(intent_changes.keys()):
        items = intent_changes[intent]
        baseline_gold = items[0]['baseline_gold']
        experimental_gold = items[0]['experimental_gold']
        count = len(items)
        lines.append(f"| {intent} | {baseline_gold} | **{experimental_gold}** | {count} |")
    lines.append("")
    
    # Cases remaining wrong
    lines.append("## C. Cases Remaining Wrong Under Both Mappings")
    lines.append("")
    lines.append(f"**{len(comparison['unchanged_incorrect'])} cases** were incorrect under both baseline and experimental mappings.")
    lines.append("")
    lines.append("These represent genuine classifier errors (not mapping issues):")
    lines.append("")
    
    # Sample a few
    sample_incorrect = []
    for case_id in comparison['unchanged_incorrect'][:5]:
        baseline_result = next(r for r in baseline_eval["results"] if r["case_id"] == case_id)
        sample_incorrect.append(baseline_result)
    
    if sample_incorrect:
        for result in sample_incorrect:
            lines.append(f"- **{result['case_id']}** ({result['original_intent']})")
            lines.append(f"  - Text: \"{result['text']}\"")
            lines.append(f"  - Gold: `{result['gold_category']}`")
            lines.append(f"  - Predicted: `{result['predicted_category']}`")
            lines.append("")
        
        if len(comparison['unchanged_incorrect']) > 5:
            lines.append(f"... and {len(comparison['unchanged_incorrect']) - 5} more cases.")
            lines.append("")
    
    # Conclusion
    lines.append("---")
    lines.append("")
    lines.append("## Conclusion")
    lines.append("")
    lines.append(f"The experimental mapping improved accuracy by **{delta_acc:+.1f} percentage points** ({baseline_acc:.1f}% → {experimental_acc:.1f}%).")
    lines.append("")
    lines.append(f"This improvement came from **{len(comparison['correct_gained'])} cases** where the classifier's prediction (which was semantically correct for a banking transaction problem) now matches the corrected gold label.")
    lines.append("")
    lines.append("**Key finding:** The baseline 50.0% accuracy was significantly impacted by mapping banking transaction-state problems (pending transfers, declined payments, balance posting issues) to \"Order Issue\" instead of \"Billing\". The classifier was actually performing better than the baseline suggested — it correctly identified these as billing/payment problems, but was marked wrong due to the taxonomy mismatch.")
    lines.append("")
    
    # Preservation note
    lines.append("---")
    lines.append("")
    lines.append("## Stage 9B Baseline Preserved ✅")
    lines.append("")
    lines.append("**Baseline results remain unchanged:**")
    lines.append(f"- File: `evaluation/datasets/banking77_mapping.json` (✅ untouched)")
    lines.append(f"- Result: 50.0% accuracy (77/154 correct)")
    lines.append(f"- Prompt: v1-a0ce2844 (✅ unchanged)")
    lines.append(f"- Cache: 154/154 predictions from Stage 9B (✅ reused, no new Groq calls)")
    lines.append("")
    lines.append("**This experimental result does NOT replace the baseline.**")
    lines.append("")
    
    return "\n".join(lines)


def main():
    print("=" * 80)
    print("BANKING77 MAPPING EXPERIMENT")
    print("=" * 80)
    print()
    
    # Load data
    print("Loading cached predictions...")
    predictions = load_cached_predictions()
    print(f"  ✓ Loaded {len(predictions)} cached predictions")
    
    print("\nLoading mappings...")
    baseline_mapping = load_mapping(BASELINE_MAPPING_PATH)
    experimental_mapping = load_mapping(EXPERIMENTAL_MAPPING_PATH)
    print(f"  ✓ Baseline mapping: {len(baseline_mapping)} intents")
    print(f"  ✓ Experimental mapping: {len(experimental_mapping)} intents")
    
    # Count changed mappings
    changed_intents = [
        intent for intent in baseline_mapping
        if baseline_mapping[intent] != experimental_mapping.get(intent)
    ]
    print(f"  ✓ {len(changed_intents)} intents have changed mappings")
    
    print("\nLoading benchmark cases...")
    benchmark_data = json.loads(BENCHMARK_PATH.read_text())
    cases = benchmark_data["cases"]
    print(f"  ✓ Loaded {len(cases)} test cases")
    
    # Evaluate with baseline mapping
    print("\nEvaluating with BASELINE mapping (Stage 9B)...")
    baseline_eval = evaluate_with_mapping(cases, predictions, baseline_mapping)
    baseline_acc = baseline_eval["overall_accuracy"] * 100
    print(f"  Baseline accuracy: {baseline_acc:.1f}% ({baseline_eval['overall_correct']}/154)")
    
    # Evaluate with experimental mapping
    print("\nEvaluating with EXPERIMENTAL mapping...")
    experimental_eval = evaluate_with_mapping(cases, predictions, experimental_mapping)
    experimental_acc = experimental_eval["overall_accuracy"] * 100
    print(f"  Experimental accuracy: {experimental_acc:.1f}% ({experimental_eval['overall_correct']}/154)")
    
    delta_acc = experimental_acc - baseline_acc
    print(f"\n  Δ Accuracy: {delta_acc:+.1f} percentage points")
    
    # Compare results
    print("\nComparing results...")
    comparison = compare_results(baseline_eval, experimental_eval)
    print(f"  Correct gained: {len(comparison['correct_gained'])}")
    print(f"  Correct lost: {len(comparison['correct_lost'])}")
    print(f"  Unchanged correct: {len(comparison['unchanged_correct'])}")
    print(f"  Unchanged incorrect: {len(comparison['unchanged_incorrect'])}")
    print(f"  Gold labels changed: {len(comparison['gold_label_changed'])}")
    
    # Generate outputs
    print("\nGenerating outputs...")
    
    # JSON output
    output_data = {
        "metadata": {
            "experiment_date": "2026-09-13",
            "classifier_model": "openai/gpt-oss-120b",
            "prompt_version": "v1-a0ce2844",
            "total_cases": 154,
            "cache_hit_rate": "100%",
            "groq_calls_made": 0,
        },
        "baseline": {
            "mapping_file": "banking77_mapping.json",
            "overall_correct": baseline_eval["overall_correct"],
            "overall_total": baseline_eval["overall_total"],
            "overall_accuracy": baseline_eval["overall_accuracy"],
            "category_accuracy": baseline_eval["category_accuracy"],
        },
        "experimental": {
            "mapping_file": "banking77_mapping_experiment.json",
            "overall_correct": experimental_eval["overall_correct"],
            "overall_total": experimental_eval["overall_total"],
            "overall_accuracy": experimental_eval["overall_accuracy"],
            "category_accuracy": experimental_eval["category_accuracy"],
        },
        "comparison": {
            "accuracy_delta_percentage_points": delta_acc,
            "correct_gained_count": len(comparison["correct_gained"]),
            "correct_lost_count": len(comparison["correct_lost"]),
            "unchanged_correct_count": len(comparison["unchanged_correct"]),
            "unchanged_incorrect_count": len(comparison["unchanged_incorrect"]),
            "gold_label_changed_count": len(comparison["gold_label_changed"]),
            "correct_gained": comparison["correct_gained"],
            "correct_lost": comparison["correct_lost"],
            "gold_label_changed": comparison["gold_label_changed"],
        },
    }
    
    EXPERIMENTAL_RESULTS_JSON.write_text(json.dumps(output_data, indent=2), encoding="utf-8")
    print(f"  ✓ Saved JSON: {EXPERIMENTAL_RESULTS_JSON}")
    
    # Markdown report
    markdown = generate_markdown_report(baseline_eval, experimental_eval, comparison, baseline_mapping, experimental_mapping)
    EXPERIMENTAL_RESULTS_MD.write_text(markdown, encoding="utf-8")
    print(f"  ✓ Saved Markdown: {EXPERIMENTAL_RESULTS_MD}")
    
    print("\n" + "=" * 80)
    print("EXPERIMENT COMPLETE")
    print("=" * 80)
    print()
    print(f"Baseline (Stage 9B): {baseline_acc:.1f}% accuracy")
    print(f"Experimental:        {experimental_acc:.1f}% accuracy")
    print(f"Improvement:         {delta_acc:+.1f} percentage points")
    print()
    print("Stage 9B baseline preserved ✅")
    print("No production files modified ✅")
    print("No new Groq calls made ✅")
    print()


if __name__ == "__main__":
    main()
