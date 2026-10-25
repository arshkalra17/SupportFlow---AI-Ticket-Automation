"""Retrieval eval — measures knowledge base search on a labelled question set.

Each question in evaluation/datasets/retrieval_v1.json names the article (and
section) that answers it. For every chunking strategy × search mode this
reports:

  Hit@1 / Hit@3 / MRR@5   is the right article at the top / in the top 3,
                          and how high (document level, chunks de-duplicated)
  Sec@1 / Sec@3           same for the exact section (section chunks only)
  Ctx@3                   the right article is among the first 3 chunks: what
                          the answering LLM sees with RAG_TOP_K = 3
  OOS AUROC               how well the top score separates in-scope questions
                          from out-of-scope ones (1.0 = a threshold exists that
                          abstains on every out-of-scope question and no other)
  Abstain                 at the best single threshold on that score: share
                          of out-of-scope questions refused / in-scope answered
  vs ref                  questions this config gets right (Hit@1) that
                          section:vector gets wrong, and the reverse
  p50 / p95 ms            per-query latency, query embedding included

Questions are split into dev (even ids) and test (odd ids). Pick a config on
dev, then confirm it on test, so the choice is not fitted to every question.

No LLM calls: this costs no API quota. It needs Postgres with pgvector; by
default it creates and uses a database named supportflow_retrieval on the
EVAL_DATABASE_URL server.

Usage:
    python -m evaluation.retrieval_eval
    python -m evaluation.retrieval_eval --configs section:hybrid,section:rerank
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evaluation.config import EVAL_DATABASE_URL, RESULTS_DIR  # noqa: E402

DATASET = ROOT / "evaluation" / "datasets" / "retrieval_v1.json"
DEFAULT_CONFIGS = [
    "document:vector",  # baseline: the original whole-article vector search
    "section:vector",
    "section:keyword",
    "section:hybrid",
    "section:rerank",
]
# Each config is compared question-by-question with this one
REFERENCE_CONFIG = "section:vector"


def retrieval_database_url() -> str:
    url = os.getenv("RETRIEVAL_EVAL_DATABASE_URL")
    if url:
        return url
    return make_url(EVAL_DATABASE_URL).set(database="supportflow_retrieval").render_as_string(hide_password=False)


def prepare_database(url: str):
    """Creates the database (if missing), pgvector and the knowledge_chunks table."""
    target = make_url(url)
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": target.database}).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    admin.dispose()

    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    from app.models import KnowledgeChunk
    from app.retrieval import ensure_term_stats

    KnowledgeChunk.__table__.create(engine, checkfirst=True)
    with engine.begin() as conn:
        ensure_term_stats(conn)
    return engine


def load_dataset(path: Path = DATASET) -> dict:
    return json.loads(path.read_text())


def split_of(question_id: str) -> str:
    return "dev" if int(question_id.lstrip("r")) % 2 == 0 else "test"


def doc_ranking(hits) -> list[str]:
    seen, out = set(), []
    for h in hits:
        if h.doc_slug not in seen:
            seen.add(h.doc_slug)
            out.append(h.doc_slug)
    return out


def auroc(positives: list[float], negatives: list[float]) -> float | None:
    """Probability a random in-scope question scores above a random out-of-scope one."""
    if not positives or not negatives:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in positives for n in negatives)
    return wins / (len(positives) * len(negatives))


def abstention_signal(hits, mode: str) -> float:
    """The score an abstain/answer threshold would be applied to.

    RRF scores depend only on ranks, so they say nothing about relevance;
    vector-family modes use the best cosine similarity instead and re-ranked
    modes use the cross-encoder score.
    """
    if not hits:
        return float("-inf")
    if mode.endswith("rerank"):
        return hits[0].scores["rerank"]
    if mode == "keyword":
        return hits[0].score
    return max((h.scores.get("vector", float("-inf")) for h in hits), default=float("-inf"))


def best_threshold(positives: list[float], negatives: list[float]) -> dict | None:
    """The threshold that maximises (out-of-scope refused + in-scope answered)."""
    if not positives or not negatives:
        return None
    best = None
    for t in sorted(set(positives) | set(negatives)):
        refused = sum(n < t for n in negatives) / len(negatives)
        answered = sum(p >= t for p in positives) / len(positives)
        if best is None or refused + answered > best["refused"] + best["answered"]:
            best = {"threshold": t, "refused": refused, "answered": answered}
    return best


def score_question(q: dict, hits) -> dict:
    docs = doc_ranking(hits)
    rank = docs.index(q["doc"]) + 1 if q["doc"] in docs else None
    chunk_docs = [h.doc_slug for h in hits]
    sections = [(h.doc_slug, h.heading) for h in hits]
    gold = (q["doc"], q["section"])
    sec_rank = sections.index(gold) + 1 if gold in sections else None
    return {
        "id": q["id"],
        "type": q["type"],
        "split": split_of(q["id"]),
        "doc_rank": rank,
        "section_rank": sec_rank,
        "chunk_rank": chunk_docs.index(q["doc"]) + 1 if q["doc"] in chunk_docs else None,
        "top": [f"{h.doc_slug}#{h.heading}" if h.heading else h.doc_slug for h in hits[:3]],
    }


def summarise(rows: list[dict], has_sections: bool) -> dict:
    n = len(rows)
    if not n:
        return {}

    def hit(key, k):
        return sum(1 for r in rows if r[key] is not None and r[key] <= k) / n

    out = {
        "n": n,
        "hit@1": hit("doc_rank", 1),
        "hit@3": hit("doc_rank", 3),
        "mrr@5": sum(1 / r["doc_rank"] for r in rows if r["doc_rank"] and r["doc_rank"] <= 5) / n,
        "ctx@3": hit("chunk_rank", 3),
    }
    if has_sections:
        out["sec@1"] = hit("section_rank", 1)
        out["sec@3"] = hit("section_rank", 3)
    return out


def run_config(conn, config: str, dataset: dict, articles, indexed: dict) -> dict:
    from app.retrieval import chunk_article, index_chunks, timed_search

    strategy, mode = config.split(":")
    if indexed.get("strategy") != strategy:
        chunks = [c for a in articles for c in chunk_article(a, strategy)]
        indexed["count"] = index_chunks(conn, chunks)
        indexed["strategy"] = strategy
        conn.commit()

    rows, latencies, in_scope_signal = [], [], []
    for q in dataset["questions"]:
        hits, ms = timed_search(conn, q["query"], mode, top_k=5)
        latencies.append(ms)
        rows.append(score_question(q, hits))
        in_scope_signal.append(abstention_signal(hits, mode))

    oos_signal = []
    for query in dataset["out_of_scope"]:
        hits, ms = timed_search(conn, query, mode, top_k=5)
        latencies.append(ms)
        oos_signal.append(abstention_signal(hits, mode))

    has_sections = strategy != "document"
    latencies.sort()
    by_type = {
        t: summarise([r for r in rows if r["type"] == t], has_sections)
        for t in sorted({r["type"] for r in rows})
    }
    return {
        "config": config,
        "chunks": indexed["count"],
        "all": summarise(rows, has_sections),
        "dev": summarise([r for r in rows if r["split"] == "dev"], has_sections),
        "test": summarise([r for r in rows if r["split"] == "test"], has_sections),
        "by_type": by_type,
        "oos_auroc": auroc(in_scope_signal, oos_signal),
        "abstain": best_threshold(in_scope_signal, oos_signal),
        "correct": {r["id"]: r["doc_rank"] == 1 for r in rows},
        "p50_ms": statistics.median(latencies),
        "p95_ms": latencies[int(0.95 * (len(latencies) - 1))],
        "misses": [r for r in rows if r["doc_rank"] != 1],
    }


def _pct(v) -> str:
    return "—" if v is None else f"{100 * v:.1f}"


def render_markdown(results: list[dict], meta: dict) -> str:
    lines = [
        "# Retrieval eval",
        "",
        f"Dataset `{meta['dataset']}` · {meta['questions']} questions "
        f"({meta['dev']} dev / {meta['test']} test) + {meta['out_of_scope']} out-of-scope · "
        f"corpus {meta['articles']} articles · {meta['generated_at']}",
        "",
        "Percentages are over all questions; dev and test columns show Hit@1 on each half.",
        "",
        f"`vs ref` counts questions won / lost at Hit@1 against `{REFERENCE_CONFIG}`.",
        "",
        "| Config | Chunks | Hit@1 | Hit@3 | MRR@5 | Ctx@3 | Sec@1 | Sec@3 | Dev Hit@1 | Test Hit@1 | vs ref | OOS AUROC | Abstain (refused / answered) | p50 ms | p95 ms |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    ref = next((r for r in results if r["config"] == REFERENCE_CONFIG), None)
    for r in results:
        a = r["all"]
        auc = "—" if r["oos_auroc"] is None else f"{r['oos_auroc']:.3f}"
        ab = r["abstain"]
        abstain = "—" if ab is None else f"{_pct(ab['refused'])} / {_pct(ab['answered'])} @ {ab['threshold']:.3f}"
        if ref is None or r is ref:
            vs = "—"
        else:
            wins = sum(ok and not ref["correct"][qid] for qid, ok in r["correct"].items())
            losses = sum(not ok and ref["correct"][qid] for qid, ok in r["correct"].items())
            vs = f"+{wins} / −{losses}"
        lines.append(
            f"| `{r['config']}` | {r['chunks']} | {_pct(a['hit@1'])} | {_pct(a['hit@3'])} | "
            f"{a['mrr@5']:.3f} | {_pct(a['ctx@3'])} | {_pct(a.get('sec@1'))} | {_pct(a.get('sec@3'))} | "
            f"{_pct(r['dev']['hit@1'])} | {_pct(r['test']['hit@1'])} | {vs} | "
            f"{auc} | {abstain} | "
            f"{r['p50_ms']:.0f} | {r['p95_ms']:.0f} |"
        )
    types = sorted({t for r in results for t in r["by_type"]})
    lines += ["", "## Hit@1 by question type", "", "| Config | " + " | ".join(
        f"{t} (n={results[0]['by_type'][t]['n']})" for t in types) + " |",
        "|---|" + "---|" * len(types)]
    for r in results:
        lines.append(f"| `{r['config']}` | " + " | ".join(_pct(r["by_type"][t]["hit@1"]) for t in types) + " |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--configs", default=",".join(DEFAULT_CONFIGS),
                        help="comma-separated chunking:mode pairs")
    parser.add_argument("--out", default="retrieval_latest", help="results file stem in evaluation/results/")
    args = parser.parse_args(argv)

    from app.retrieval import load_articles

    dataset = load_dataset()
    articles = load_articles()
    engine = prepare_database(retrieval_database_url())
    configs = [c.strip() for c in args.configs.split(",") if c.strip()]
    # Group by chunking strategy so each corpus is embedded once
    configs.sort(key=lambda c: c.split(":")[0])

    results, indexed = [], {}
    with engine.connect() as conn:
        # Warm up both models so the first config's latency is not a cold start
        from app.retrieval import get_reranker
        from app.embeddings import generate_embeddings

        generate_embeddings(["warm up"])
        if any(c.endswith("rerank") for c in configs):
            get_reranker().predict([("warm", "up")])
        for config in configs:
            print(f"→ {config}", flush=True)
            results.append(run_config(conn, config, dataset, articles, indexed))
            a = results[-1]["all"]
            print(f"   hit@1 {_pct(a['hit@1'])}  hit@3 {_pct(a['hit@3'])}  mrr {a['mrr@5']:.3f}  "
                  f"auroc {results[-1]['oos_auroc']:.3f}  p50 {results[-1]['p50_ms']:.0f}ms", flush=True)
    order = {c: i for i, c in enumerate(args.configs.split(","))}
    results.sort(key=lambda r: order.get(r["config"], 99))

    qs = dataset["questions"]
    meta = {
        "dataset": dataset["version"],
        "questions": len(qs),
        "dev": sum(split_of(q["id"]) == "dev" for q in qs),
        "test": sum(split_of(q["id"]) == "test" for q in qs),
        "out_of_scope": len(dataset["out_of_scope"]),
        "articles": len(articles),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
    (RESULTS_DIR / f"{args.out}.json").write_text(json.dumps({"meta": meta, "results": results}, indent=2))
    md = render_markdown(results, meta)
    (RESULTS_DIR / f"{args.out}.md").write_text(md)
    print("\n" + md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
