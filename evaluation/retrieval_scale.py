"""Retrieval scale check — does search hold up as the knowledge base grows?

Adds N synthetic distractor chunks (product-page style text that shares the
KB's vocabulary — batteries, shipping, warranty, orders — but never answers a
labelled question) next to the real section chunks, then measures at each
size:

  Hit@1             vector and rerank, over exact (brute-force) search and
                    over the HNSW index, with the distractors present
  ANN recall@10     share of the exact top 10 that the HNSW index also returns,
                    at the default hnsw.ef_search (40) and a wider one
  p50 ms            query latency for each

  Scoped           HNSW over a partial index holding only the real
                    (policy) chunks, queried with the same WHERE clause

Exact Hit@1 isolates what the extra content does to ranking; the gap
between exact and HNSW is what the approximate index costs. The real
sections form a small cluster far from the product pages (mean cosine 0.08
between them vs ~0.22 within each), and greedy HNSW search over one shared
graph rarely reaches it: raising ef_search, building the index after loading,
iterative scans and a denser graph (m=32/48) all left recall@10 near 50%.
A partial index per content kind gives the small cluster its own graph.

No LLM calls. Uses the same database as evaluation/retrieval_eval.py.

Usage:
    python -m evaluation.retrieval_scale --sizes 1000,10000,50000
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evaluation.config import RESULTS_DIR  # noqa: E402
from evaluation.retrieval_eval import load_dataset, prepare_database, retrieval_database_url  # noqa: E402

PRODUCTS = ["wireless earbuds", "4K monitor", "mechanical keyboard", "laptop stand", "USB-C hub", "smart speaker",
            "fitness tracker", "gaming mouse", "portable SSD", "webcam", "e-reader", "action camera",
            "robot vacuum", "air purifier", "electric kettle", "desk lamp", "power bank", "tablet case",
            "noise-cancelling headphones", "smartwatch", "drawing tablet", "router", "dash cam", "projector"]
BRANDS = ["Nordline", "Vexa", "Orbit", "Kestrel", "Lumio", "Pallas", "Tandem", "Quill", "Arbor", "Helix"]
FACTS = [
    "The {p} ships in a recycled box and includes a quick-start guide.",
    "Battery life on the {p} is rated at {n} hours with typical use.",
    "The {b} {p} weighs {n}0 grams and comes in three colours.",
    "Customers often pair the {p} with a {q} for a tidier desk setup.",
    "Firmware updates for the {p} are installed from the companion app.",
    "The {p} supports Bluetooth 5.{d} and connects to two devices at once.",
    "Our review team rated the {b} {p} {d} out of 5 for build quality.",
    "In the box: the {p}, a charging cable and a {n}-page manual.",
    "The {p} is popular as a gift and is often bought during holiday sales.",
    "Order the {p} before noon for the earliest dispatch shown at checkout.",
    "Reviewers note the {p}'s setup takes under {n} minutes.",
    "The {b} {p} has a one-year manufacturer warranty, registered online.",
]


def synthetic_chunks(n: int, seed: int = 7):
    from app.retrieval import Chunk

    rng = random.Random(seed)
    out = []
    for i in range(n):
        p, q, b = rng.choice(PRODUCTS), rng.choice(PRODUCTS), rng.choice(BRANDS)
        sentences = rng.sample(FACTS, 3)
        body = " ".join(s.format(p=p, q=q, b=b, n=rng.randint(2, 40), d=rng.randint(1, 4)) for s in sentences)
        out.append(Chunk(f"synthetic-{i}", f"{b} {p.title()}", rng.choice(["Overview", "Specs", "In the box", "Reviews"]),
                         body, "synthetic"))
    return out


def insert_distractors(conn, chunks, batch: int = 2000) -> None:
    from app.retrieval import index_chunks

    for start in range(0, len(chunks), batch):
        index_chunks(conn, chunks[start:start + batch], replace=False)
        conn.commit()


EF_SEARCH = (40, 200)
SCOPE = "source <> 'synthetic'"
SCOPED_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS ix_scale_scoped_hnsw ON knowledge_chunks "
    f"USING hnsw (embedding vector_cosine_ops) WHERE {SCOPE}"
)


def scoped_search(conn, query_vector, k):
    from app.retrieval import _COLUMNS, Hit

    rows = conn.execute(
        text(f"SELECT {_COLUMNS}, 1 - (embedding <=> CAST(:v AS vector)) AS sim FROM knowledge_chunks "
             f"WHERE {SCOPE} ORDER BY embedding <=> CAST(:v AS vector) LIMIT :k"),
        {"v": str(query_vector), "k": k},
    ).fetchall()
    return [Hit(r.id, r.doc_slug, r.doc_title, r.heading, r.content, r.source, float(r.sim), {"vector": float(r.sim)})
            for r in rows]


def measure(conn, dataset, query_vectors) -> dict:
    from app.retrieval import RERANK_CANDIDATES, rerank, vector_search

    qs = dataset["questions"]

    def run(label, setup, scoped=False):
        conn.execute(text(setup))
        ms, results = [], []
        for q, v in zip(qs, query_vectors):
            t = time.perf_counter()
            if scoped:
                results.append(scoped_search(conn, v, RERANK_CANDIDATES))
            else:
                results.append(vector_search(conn, q["query"], RERANK_CANDIDATES, query_vector=v))
            ms.append((time.perf_counter() - t) * 1000)
        conn.rollback()
        return label, results, statistics.median(ms)

    runs = [run("exact", "SET enable_indexscan = off")] + [
        run(f"hnsw{ef}", f"SET enable_indexscan = on; SET hnsw.ef_search = {ef}") for ef in EF_SEARCH
    ]
    runs.append(run("scoped", "SET enable_indexscan = on; SET hnsw.ef_search = 40", scoped=True))
    exact = runs[0][1]
    row = {}
    for label, results, p50 in runs:
        row[f"{label}_hit@1"] = sum(bool(r) and r[0].doc_slug == q["doc"] for q, r in zip(qs, results)) / len(qs)
        row[f"{label}_rerank_hit@1"] = sum(
            bool(r) and rerank(q["query"], r)[0].doc_slug == q["doc"] for q, r in zip(qs, results)
        ) / len(qs)
        row[f"{label}_recall@10"] = statistics.mean(
            len({h.chunk_id for h in e} & {h.chunk_id for h in r}) / len(e) for e, r in zip(exact, results)
        )
        row[f"{label}_p50_ms"] = p50
    return row


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sizes", default="1000,10000,50000", help="distractor counts, ascending")
    args = parser.parse_args(argv)
    sizes = sorted(int(s) for s in args.sizes.split(","))

    from app.embeddings import generate_embeddings
    from app.retrieval import chunk_article, index_chunks, load_articles

    dataset = load_dataset()
    engine = prepare_database(retrieval_database_url())
    query_vectors = generate_embeddings([q["query"] for q in dataset["questions"]])
    distractors = synthetic_chunks(sizes[-1])

    rows = []
    with engine.connect() as conn:
        real = [c for a in load_articles() for c in chunk_article(a, "section")]
        index_chunks(conn, real)
        conn.execute(text(SCOPED_INDEX_DDL))
        conn.commit()
        added = 0
        for size in [0] + sizes:
            if size > added:
                print(f"→ embedding distractors {added}..{size}", flush=True)
                insert_distractors(conn, distractors[added:size])
                added = size
                conn.execute(text("ANALYZE knowledge_chunks"))
                conn.commit()
            row = {"distractors": size, "chunks": len(real) + size, **measure(conn, dataset, query_vectors)}
            rows.append(row)
            print(f"   {row}", flush=True)
        # Leave the database as retrieval_eval expects it
        conn.execute(text("DROP INDEX IF EXISTS ix_scale_scoped_hnsw"))
        index_chunks(conn, real)
        conn.commit()

    ef_low, ef_high = EF_SEARCH
    lines = [
        "# Retrieval scale check",
        "",
        f"{len(dataset['questions'])} labelled questions; synthetic product-page distractors added next to the "
        f"real section chunks · pgvector HNSW (m=16, ef_construction=64) · "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        f"| Chunks | Exact Hit@1 (vector / rerank) | HNSW ef={ef_low} Hit@1 (vector / rerank) | "
        f"HNSW ef={ef_high} Hit@1 (vector / rerank) | Recall@10 ef={ef_low} / ef={ef_high} | "
        f"p50 ms exact / ef={ef_low} / ef={ef_high} | Scoped HNSW Hit@1 (vector / rerank) | Scoped p50 ms |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        def pair(label):
            return f"{100 * r[label + '_hit@1']:.1f} / {100 * r[label + '_rerank_hit@1']:.1f}"
        lines.append(
            f"| {r['chunks']:,} | {pair('exact')} | {pair(f'hnsw{ef_low}')} | {pair(f'hnsw{ef_high}')} | "
            f"{100 * r[f'hnsw{ef_low}_recall@10']:.1f} / {100 * r[f'hnsw{ef_high}_recall@10']:.1f} | "
            f"{r['exact_p50_ms']:.1f} / {r[f'hnsw{ef_low}_p50_ms']:.1f} / {r[f'hnsw{ef_high}_p50_ms']:.1f} | "
            f"{pair('scoped')} | {r['scoped_p50_ms']:.1f} |"
        )
    md = "\n".join(lines) + "\n"
    (RESULTS_DIR / "retrieval_scale.md").write_text(md)
    (RESULTS_DIR / "retrieval_scale.json").write_text(json.dumps(rows, indent=2))
    print("\n" + md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
