"""
Golden-set evaluation: runs every case in evaluation/datasets/golden_v1.json
through the production pipeline (run_supportflow), scores it, and optionally
gates against a stored baseline.

Usage:
    python -m evaluation.golden_eval                      # full run, writes report
    python -m evaluation.golden_eval --smoke --gate       # routine gate: fixed 26-case set
    python -m evaluation.golden_eval --sample 26          # cheap run, 2 per category
    python -m evaluation.golden_eval --category refund    # one category
    python -m evaluation.golden_eval --ids refund-004,authz-003
    python -m evaluation.golden_eval --no-judge           # deterministic checks only
    python -m evaluation.golden_eval --update-baseline    # store this run as the baseline
    python -m evaluation.golden_eval --gate               # exit 1 on regression vs baseline
    python -m evaluation.golden_eval --resume             # continue a run stopped by the daily quota
    python -m evaluation.golden_eval --provider gemini    # run the whole set on one provider
    python -m evaluation.golden_eval --smoke --routing-mode agent   # search_policy agent instead of classify→route

Requires the chosen provider's API key (GROQ_API_KEY or GEMINI_API_KEY;
the judge always uses GROQ_API_KEY) and the eval database (EVAL_DATABASE_URL).
The provider is pinned for the whole run: no fallback, so the numbers
describe one model. Each case
is run against freshly seeded fixtures.

Free-tier budget: the binding Groq limit is tokens per day (200K/day per
model), not requests. The classifier runs on gpt-oss-20b and the judge on
qwen3.8-27b, each with its own quota, so gpt-oss-120b only carries the
tool loop and RAG answers. The report shows tokens per case. The smoke
set fits easily in a day; a full run may not, and when a daily quota runs
out the run stops, keeps its progress, and `--resume` continues it after
the reset.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

from evaluation.config import EVAL_DATABASE_URL, GROQ_CALL_DELAY_SECONDS, RESULTS_DIR  # noqa: E402

LATEST_JSON = RESULTS_DIR / "golden_latest.json"
LATEST_MD = RESULTS_DIR / "golden_latest.md"


# Two tiers. The smoke set (2 cases per category, fixed) fits well inside
# one day's free-tier quota and is the routine regression gate. The full
# set spans more than a day's quota and is run occasionally with --resume.
SMOKE_SIZE = 26


def baseline_path(provider: str, tier: str = "full", mode: str = "router"):
    """Each provider, tier and routing mode has its own baseline: comparing
    Gemini against a Groq baseline, or the smoke set against the full set,
    would flag differences in what was measured as regressions."""
    name = "golden_baseline"
    if provider != "groq":
        name += f"_{provider}"
    if mode != "router":
        name += f"_{mode}"
    if tier != "full":
        name += f"_{tier}"
    return RESULTS_DIR / f"{name}.json"



def progress_path(provider: str, tier: str, mode: str = "router"):
    """One line per finished case, so a run cut short by the daily token
    quota can continue later with --resume instead of starting over. One
    file per provider and tier, so starting one run never wipes another's
    progress."""
    return RESULTS_DIR / baseline_path(provider, tier, mode).name.replace(
        "golden_baseline", "golden_progress").replace(".json", ".jsonl")

MAX_ATTEMPTS = 4


def _is_rate_limited(text: str | None) -> bool:
    text = (text or "").lower()
    return "429" in text or "rate limit" in text or "rate_limit" in text


def _is_daily_quota(text: str | None) -> bool:
    """A per-day limit won't clear by retrying in seconds, unlike the
    per-minute ones."""
    from app.llm_client import is_daily_quota
    return is_daily_quota(text or "")


class DailyQuotaExhausted(RuntimeError):
    pass


def _git_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return None


def _prepare_database():
    """Creates the schema and knowledge base in the eval DB if missing.

    Fixtures are seeded per case by seed_fixtures(); knowledge documents are
    left in place between cases.
    """
    from sqlalchemy import text

    import app.models  # noqa: F401  (registers tables)
    from app.database import Base, SessionLocal, engine
    from app.kb import seed_knowledge_base
    from app.retrieval import ensure_term_stats

    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
        conn.commit()
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        ensure_term_stats(conn)

    db = SessionLocal()
    seed_knowledge_base(db)
    return db


def stratified_sample(cases: list[dict], n: int) -> list[dict]:
    """Deterministic sample of about n cases, round-robin across categories,
    so a cheap run still touches every category."""
    by_category: dict[str, list] = {}
    for c in cases:
        by_category.setdefault(c["category"], []).append(c)
    picked, queues = [], list(by_category.values())
    while len(picked) < n and any(queues):
        for q in queues:
            if q and len(picked) < n:
                picked.append(q.pop(0))
    return picked


def _select(cases: list[dict], args) -> list[dict]:
    if args.ids:
        wanted = set(args.ids.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    if args.category:
        cases = [c for c in cases if c["category"] == args.category]
    if args.sample:
        cases = stratified_sample(cases, args.sample)
    if args.limit:
        cases = cases[: args.limit]
    return cases


def _run_case(case: dict, fixtures: dict, db, use_judge: bool) -> tuple[dict, dict, dict | None]:
    from app.graph import run_supportflow
    from evaluation.golden import history_newest_first, seed_fixtures
    from evaluation.golden_scoring import observe, score_case
    from evaluation.judge import judge_case

    from app.llm_client import get_usage, reset_usage

    reset_usage()
    for attempt in range(1, MAX_ATTEMPTS + 1):
        seed_fixtures(db, fixtures)
        ctx = case["context"]
        started = time.perf_counter()
        state = run_supportflow(
            customer_message=case["message"],
            authenticated_customer_id=case["customer_id"],
            conversation_history=history_newest_first(case),
            active_order_id=ctx.get("active_order_id"),
            active_ticket_id=ctx.get("active_ticket_id"),
            last_action=ctx.get("last_action"),
        )
        if _is_daily_quota(state.get("error")):
            raise DailyQuotaExhausted(state["error"])
        if _is_rate_limited(state.get("error")) and attempt < MAX_ATTEMPTS:
            time.sleep(10 * attempt)
            continue
        break

    obs = observe(state)
    # Pipeline wall time of the attempt that counted (includes any wait on a
    # per-minute rate limit) and tokens (the judge calls Groq directly,
    # outside the chain)
    obs["pipeline_ms"] = round((time.perf_counter() - started) * 1000)
    obs["usage"] = get_usage()
    judge = None
    # Only spend a judge call when the deterministic checks passed; a case
    # that already failed cannot be rescued by the judge.
    if use_judge and score_case(case, obs)["passed"]:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                judge = judge_case(case, obs)
                break
            except Exception as err:  # judge failures must not sink the run
                if _is_daily_quota(str(err)):
                    raise DailyQuotaExhausted(str(err)) from err
                if _is_rate_limited(str(err)) and attempt < MAX_ATTEMPTS:
                    time.sleep(10 * attempt)
                    continue
                judge = {"score": None, "pass": None, "reason": f"judge error: {err}"}
                break
    return state, obs, judge


def _token_summary(details: list[dict]) -> dict:
    """Pipeline token use per model, plus the per-case mean, so quota and
    cost changes show up next to the quality metrics."""
    by_model: dict[str, dict[str, int]] = {}
    for d in details:
        for model, u in (d["observation"].get("usage") or {}).items():
            t = by_model.setdefault(model, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0})
            for k in t:
                t[k] += u.get(k, 0)
    total = sum(t["prompt_tokens"] + t["completion_tokens"] for t in by_model.values())
    latencies = sorted(d["observation"]["pipeline_ms"] for d in details if d["observation"].get("pipeline_ms"))
    return {
        "total": total,
        "per_case_mean": round(total / len(details)) if details else None,
        "by_model": by_model,
        "pipeline_p50_ms": latencies[len(latencies) // 2] if latencies else None,
    }


def _write_report(meta: dict, metrics: dict, scores: list[dict], regressions: list[str] | None) -> None:
    def pct(v):
        return "—" if v is None else f"{v:.1%}"

    o = metrics["overall"]
    lines = [
        "# Golden-set evaluation",
        "",
        f"Run: {meta['timestamp']} · {meta['provider']} `{meta['model']}` · routing `{meta.get('routing_mode', 'router')}`"
        f" · dataset `{meta['dataset_version']}`"
        f" · commit `{meta['git_sha']}` · judge `{meta['judge_model'] or 'off'}`",
        "",
        *(["**INCOMPLETE: the daily quota ran out before every case ran.**", ""]
          if meta.get("incomplete") else []),
        "Judge pass rate is measured over cases that passed the deterministic checks "
        "(failed cases are not sent to the judge).",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Cases | {o['cases']} |",
        f"| **Pass rate** | **{pct(o['pass_rate'])}** |",
        f"| Route accuracy | {pct(o['route_accuracy'])} |",
        f"| Tool recall | {pct(o['tool_recall'])} |",
        f"| Argument accuracy | {pct(o['args_accuracy'])} |",
        f"| Outcome accuracy | {pct(o['outcome_accuracy'])} |",
        f"| Policy retrieved when needed (mixed cases) | {pct(o.get('policy_accuracy'))} |",
        f"| Judge pass rate | {pct(o['judge_pass_rate'])} |",
        f"| Forbidden-tool violations | {o['forbidden_tool_violations']} |",
        f"| Unsafe-phrase violations | {o['unsafe_phrase_violations']} |",
        f"| Actions stopped by the action guard | {o.get('guard_blocked_actions', 0)} |",
        f"| Tokens per case (mean) | {meta['tokens']['per_case_mean']} |",
        *[f"| `{model}` tokens per case | {round((u['prompt_tokens'] + u['completion_tokens']) / o['cases'])} |"
          for model, u in sorted(meta['tokens']['by_model'].items()) if o['cases']],
        f"| Pipeline p50 latency | {meta['tokens'].get('pipeline_p50_ms') or '—'} ms |",
        "",
        "## By category",
        "",
        "| Category | Cases | Pass | Outcome | Judge | Safety violations |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, m in metrics["by_category"].items():
        lines.append(
            f"| {name} | {m['cases']} | {pct(m['pass_rate'])} | {pct(m['outcome_accuracy'])} | "
            f"{pct(m['judge_pass_rate'])} | {m['forbidden_tool_violations'] + m['unsafe_phrase_violations']} |"
        )
    lines += ["", "## By difficulty", "", "| Difficulty | Cases | Pass |", "|---|---:|---:|"]
    for name, m in metrics["by_difficulty"].items():
        lines.append(f"| {name} | {m['cases']} | {pct(m['pass_rate'])} |")

    if regressions is not None:
        lines += ["", "## Gate", ""]
        lines += [f"- ❌ {r}" for r in regressions] or ["- ✅ No regressions against baseline"]

    failed = [s for s in scores if not s["passed"]]
    lines += ["", f"## Failures ({len(failed)})", ""]
    for s in failed:
        why = []
        for key in ("route_ok", "tools_ok", "args_ok", "outcome_ok", "policy_ok", "judge_pass"):
            if s.get(key) is False:
                why.append("no policy retrieved" if key == "policy_ok" else key.replace("_ok", "").replace("_pass", ""))
        if s["forbidden_called"]:
            why.append(f"FORBIDDEN {s['forbidden_called']}")
        if s.get("guard_blocked"):
            why.append(f"guard stopped {s['guard_blocked']}")
        if s["unsafe_phrases"]:
            why.append(f"UNSAFE {s['unsafe_phrases']}")
        detail = f"expected {s['expected_outcome']}, got {s['actual_outcome']}"
        if s["judge_reason"]:
            detail += f"; judge: {s['judge_reason']}"
        lines.append(f"- `{s['id']}` — {', '.join(why)} ({detail})")

    LATEST_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the golden-set evaluation.")
    parser.add_argument("--ids", help="Comma-separated case ids")
    parser.add_argument("--category", help="Only run one category")
    parser.add_argument("--smoke", action="store_true",
                        help=f"Fixed {SMOKE_SIZE}-case smoke set (2 per category); can be gated and baselined")
    parser.add_argument("--sample", type=int, help="Stratified sample of N cases across categories")
    parser.add_argument("--limit", type=int, help="Run at most N cases")
    parser.add_argument("--no-judge", action="store_true", help="Skip the LLM judge")
    parser.add_argument("--update-baseline", action="store_true", help="Save this run as the baseline")
    parser.add_argument("--gate", action="store_true", help="Exit 1 if metrics regress vs baseline")
    parser.add_argument("--tolerance", type=float, default=0.03, help="Allowed drop in rate metrics")
    parser.add_argument("--provider", default="groq", choices=["groq", "gemini"],
                        help="LLM provider for the pipeline, pinned for the whole run (default groq)")
    parser.add_argument("--routing-mode", default="router", choices=["router", "agent"],
                        help="router: classify then route to RAG or tools (default); "
                             "agent: one tool loop with a search_policy tool")
    parser.add_argument("--resume", action="store_true",
                        help="Skip cases already finished in golden_progress.jsonl (same dataset and judge setting)")
    args = parser.parse_args()

    key_env = {"groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY"}[args.provider]
    if not os.getenv(key_env):
        print(f"{key_env} is not set; the golden eval needs a live model.", file=sys.stderr)
        return 2
    if not args.no_judge and not os.getenv("GROQ_API_KEY"):
        print("GROQ_API_KEY is not set; the judge needs it (or pass --no-judge).", file=sys.stderr)
        return 2

    # Point the app at the eval database and pin the provider before any
    # app module is imported.
    os.environ["DATABASE_URL"] = EVAL_DATABASE_URL
    os.environ["LLM_PROVIDERS"] = args.provider
    os.environ["ROUTING_MODE"] = args.routing_mode

    from app.llm_client import model_for

    from evaluation.golden import load_golden
    from evaluation.golden_scoring import aggregate, compare_to_baseline, score_case
    from evaluation.judge import JUDGE_MODEL

    golden = load_golden()
    if args.smoke:
        if args.ids or args.category or args.sample or args.limit:
            print("--smoke can't be combined with --ids/--category/--sample/--limit.", file=sys.stderr)
            return 2
        cases = stratified_sample(golden["cases"], SMOKE_SIZE)
    else:
        cases = _select(golden["cases"], args)
    partial = len(cases) != len(golden["cases"])
    tier = "smoke" if args.smoke else ("full" if not partial else None)
    if (args.update_baseline or args.gate) and tier is None:
        print("--update-baseline and --gate need the full dataset or --smoke "
              "(no --ids/--category/--sample/--limit).",
              file=sys.stderr)
        return 2

    use_judge = not args.no_judge
    run_key = {"dataset_version": golden["version"], "judge": use_judge, "provider": args.provider}
    if args.routing_mode != "router":
        # Router runs keep the old key, so progress saved before this flag still resumes
        run_key["routing_mode"] = args.routing_mode
    done: dict[str, dict] = {}
    baseline_json = baseline_path(args.provider, tier or "full", args.routing_mode)
    progress = progress_path(args.provider, tier, args.routing_mode) if tier else None
    if args.resume and not progress:
        print("--resume only applies to full or --smoke runs.", file=sys.stderr)
        return 2
    if args.resume and progress.exists():
        for line in progress.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row["run_key"] == run_key:
                done[row["detail"]["id"]] = row["detail"]
    elif progress and progress.exists():
        progress.unlink()
    if done:
        print(f"Resuming: {sum(c['id'] in done for c in cases)} of {len(cases)} cases already done.")

    db = _prepare_database()
    details = []
    quota_error = None
    started = time.time()
    try:
        for i, case in enumerate(cases, 1):
            if case["id"] in done:
                details.append(done[case["id"]])
                continue
            try:
                state, obs, judge = _run_case(case, golden["fixtures"], db, use_judge)
            except DailyQuotaExhausted as err:
                quota_error = err
                break
            score = score_case(case, obs, judge)
            score["tokens"] = sum(u["prompt_tokens"] + u["completion_tokens"] for u in obs["usage"].values())
            detail = {"id": case["id"], "observation": obs, "score": score}
            details.append(detail)
            if progress:
                with progress.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"run_key": run_key, "detail": detail}, default=str) + "\n")
            mark = "PASS" if score["passed"] else "FAIL"
            print(f"[{i:>3}/{len(cases)}] {mark} {case['id']:<14} "
                  f"outcome={score['actual_outcome']} judge={score['judge_score']} "
                  f"tokens={score['tokens']}", flush=True)
            time.sleep(GROQ_CALL_DELAY_SECONDS)
    finally:
        db.close()
    scores = [d["score"] for d in details]

    metrics = aggregate(scores)
    meta = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provider": args.provider,
        "routing_mode": args.routing_mode,
        "model": model_for(args.provider),
        "dataset_version": golden["version"],
        "git_sha": _git_sha(),
        "judge": use_judge,
        "judge_model": JUDGE_MODEL if use_judge else None,
        "partial": partial,
        "tier": tier,
        "resumed_cases": len(done),
        "duration_s": round(time.time() - started, 1),
        "tokens": _token_summary(details),
    }

    if quota_error:
        # Report what finished, but never gate or baseline an incomplete run
        meta["incomplete"] = True
        resume_hint = " Progress is saved; rerun with --resume later." if progress else ""
        print(f"\nDaily quota exhausted ({args.provider}) after {len(details)} of {len(cases)} "
              f"cases.{resume_hint}\n{quota_error}", file=sys.stderr)

    regressions = None
    if args.gate and not quota_error:
        if not baseline_json.exists():
            print(f"No baseline at {baseline_json}; run with --update-baseline first.", file=sys.stderr)
            return 2
        baseline = json.loads(baseline_json.read_text(encoding="utf-8"))
        regressions = compare_to_baseline(metrics, baseline["metrics"], args.tolerance)

    LATEST_JSON.write_text(
        json.dumps({"meta": meta, "metrics": metrics, "cases": details}, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    _write_report(meta, metrics, scores, regressions)
    if quota_error:
        print(f"Partial report: {LATEST_MD}")
        return 3
    if args.update_baseline:
        baseline_json.write_text(json.dumps({"meta": meta, "metrics": metrics}, indent=2) + "\n",
                                 encoding="utf-8")

    o = metrics["overall"]
    print(f"\nPass rate {o['pass_rate']:.1%} over {o['cases']} cases · "
          f"forbidden-tool violations {o['forbidden_tool_violations']} · "
          f"unsafe-phrase violations {o['unsafe_phrase_violations']}")
    print(f"Report: {LATEST_MD}")
    if regressions:
        print("\nREGRESSIONS:\n  " + "\n  ".join(regressions))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
