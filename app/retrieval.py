"""Knowledge base retrieval — markdown ingestion, chunking and search.

The knowledge base lives in knowledge_base/*.md: one article per file, a
"# Title" line and "## Section" headings. Articles are split into chunks and
stored in knowledge_chunks with a vector embedding and a full-text tsvector,
so one table serves every search mode:

  vector   pgvector cosine distance over chunk embeddings
  keyword  Postgres full-text search (ts_rank_cd over an OR query)
  hybrid   vector + keyword fused with reciprocal rank fusion (RRF)
  rerank   vector candidates re-scored by a cross-encoder

evaluation/retrieval_eval.py measures each mode on a labelled question set;
evaluation/results/retrieval.md records why the app uses section chunks
with "rerank" (hybrid search was measured and did not help on this corpus).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.embeddings import generate_embeddings

KB_DIR = Path(__file__).resolve().parent.parent / "knowledge_base"

RRF_K = 60  # standard RRF constant; dampens the weight of top ranks
CANDIDATES = 20  # how many results each retriever hands to fusion
RERANK_CANDIDATES = 10  # vector hits the cross-encoder re-scores (10 beat 5 and 20 on dev)
RERANK_MODEL_NAME = "BAAI/bge-reranker-base"

_HEADING = re.compile(r"^(#{1,2})\s+(.+?)\s*$", re.M)


@dataclass
class Article:
    slug: str
    title: str
    sections: list[tuple[str, str]]  # (heading, body)
    source: str = ""


@dataclass
class Chunk:
    doc_slug: str
    doc_title: str
    heading: str | None  # None for a whole-article chunk
    content: str
    source: str = ""

    def embedding_text(self) -> str:
        """Text the embedding sees: the article title and heading give a short
        section the context it lacks on its own."""
        header = self.doc_title if self.heading is None else f"{self.doc_title} — {self.heading}"
        return f"{header}\n{self.content}"


@dataclass
class Hit:
    chunk_id: int
    doc_slug: str
    doc_title: str
    heading: str | None
    content: str
    source: str
    score: float
    scores: dict = field(default_factory=dict)  # per-retriever detail


def parse_article(path: Path) -> Article:
    """Parses one markdown article into its title and (heading, body) sections."""
    raw = path.read_text(encoding="utf-8")
    title = None
    sections: list[tuple[str, str]] = []
    matches = list(_HEADING.finditer(raw))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
        body = raw[m.end():end].strip()
        if m.group(1) == "#":
            title = m.group(2)
        else:
            sections.append((m.group(2), body))
    if not title:
        raise ValueError(f"{path.name}: missing '# Title' line")
    return Article(slug=path.stem, title=title, sections=sections, source=f"knowledge_base/{path.name}")


def load_articles(kb_dir: Path = KB_DIR) -> list[Article]:
    return [parse_article(p) for p in sorted(kb_dir.glob("*.md"))]


def chunk_article(article: Article, strategy: str = "section") -> list[Chunk]:
    """Splits an article into chunks.

    document  one chunk per article (the original design)
    section   one chunk per "## " section
    """
    if strategy == "document":
        body = "\n\n".join(f"{h}\n{b}" for h, b in article.sections)
        return [Chunk(article.slug, article.title, None, body, article.source)]
    if strategy == "section":
        return [Chunk(article.slug, article.title, h, b, article.source) for h, b in article.sections]
    raise ValueError(f"unknown chunking strategy: {strategy}")


# ── Storage ───────────────────────────────────────────────────────────

def index_chunks(conn: Connection, chunks: list[Chunk], replace: bool = True) -> int:
    """Embeds chunks and writes them to knowledge_chunks (replacing what is there)."""
    if replace:
        conn.execute(text("TRUNCATE knowledge_chunks RESTART IDENTITY"))
    vectors = generate_embeddings([c.embedding_text() for c in chunks])
    conn.execute(
        text(
            "INSERT INTO knowledge_chunks (doc_slug, doc_title, heading, content, source, embedding) "
            "VALUES (:doc_slug, :doc_title, :heading, :content, :source, :embedding)"
        ),
        [
            {
                "doc_slug": c.doc_slug,
                "doc_title": c.doc_title,
                "heading": c.heading,
                "content": c.content,
                "source": c.source,
                "embedding": str(v),
            }
            for c, v in zip(chunks, vectors)
        ],
    )
    refresh_term_stats(conn)
    return len(chunks)


# ── Retrievers ────────────────────────────────────────────────────────

_COLUMNS = "id, doc_slug, doc_title, heading, content, source"


def _hit(row, score: float, **scores) -> Hit:
    return Hit(row.id, row.doc_slug, row.doc_title, row.heading, row.content, row.source, score, scores)


def vector_search(conn: Connection, query: str, k: int = CANDIDATES, query_vector=None) -> list[Hit]:
    vec = query_vector if query_vector is not None else generate_embeddings([query])[0]
    rows = conn.execute(
        text(
            f"SELECT {_COLUMNS}, 1 - (embedding <=> CAST(:v AS vector)) AS sim "
            "FROM knowledge_chunks ORDER BY embedding <=> CAST(:v AS vector) LIMIT :k"
        ),
        {"v": str(vec), "k": k},
    ).fetchall()
    return [_hit(r, float(r.sim), vector=float(r.sim)) for r in rows]


BM25_K1 = 1.2
BM25_B = 0.75

# Document frequency per lexeme, for BM25's IDF. Postgres' own ts_rank has no
# IDF, so "order" (in most chunks) counted as much as "bitcoin" (in one).
TERM_STATS_DDL = (
    "CREATE MATERIALIZED VIEW IF NOT EXISTS knowledge_term_stats AS "
    "SELECT word AS lexeme, ndoc AS df FROM ts_stat('SELECT tsv FROM knowledge_chunks')"
)
TERM_STATS_INDEX_DDL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_knowledge_term_stats_lexeme ON knowledge_term_stats (lexeme)"
)


def ensure_term_stats(conn: Connection) -> None:
    """Creates knowledge_term_stats if missing (a schema built by create_all
    rather than the Alembic migration does not have it)."""
    conn.execute(text(TERM_STATS_DDL))
    conn.execute(text(TERM_STATS_INDEX_DDL))


def refresh_term_stats(conn: Connection) -> None:
    conn.execute(text("REFRESH MATERIALIZED VIEW knowledge_term_stats"))


_BM25_SQL = text(
    f"""
    WITH q AS (
        SELECT array_agg(lexeme) AS lexemes,
               NULLIF(replace(plainto_tsquery('english', :q)::text, '&', '|'), '')::tsquery AS tsq
        FROM unnest(to_tsvector('english', :q))
    ),
    corpus AS (SELECT count(*) AS n, avg(length(tsv)) AS avgdl FROM knowledge_chunks),
    cand AS (
        SELECT c.id, c.tsv FROM knowledge_chunks c, q
        WHERE q.tsq IS NOT NULL AND c.tsv @@ q.tsq
    ),
    scored AS (
        SELECT cand.id,
               sum(
                   ln(1 + (corpus.n - s.df + 0.5) / (s.df + 0.5))
                   * (array_length(t.positions, 1) * ({BM25_K1} + 1))
                   / (array_length(t.positions, 1)
                      + {BM25_K1} * (1 - {BM25_B} + {BM25_B} * length(cand.tsv) / corpus.avgdl))
               ) AS bm25
        FROM cand
        CROSS JOIN corpus
        CROSS JOIN q
        CROSS JOIN LATERAL unnest(cand.tsv) AS t(lexeme, positions, weights)
        JOIN knowledge_term_stats s ON s.lexeme = t.lexeme
        WHERE t.lexeme = ANY(q.lexemes)
        GROUP BY cand.id
    )
    SELECT {_COLUMNS}, scored.bm25
    FROM scored JOIN knowledge_chunks USING (id)
    ORDER BY scored.bm25 DESC
    LIMIT :k
    """
)


def keyword_search(conn: Connection, query: str, k: int = CANDIDATES) -> list[Hit]:
    """BM25 over the chunks' full-text index.

    The GIN index finds chunks sharing any query term (terms OR-ed: a
    conversational question rarely contains every word of the answer), then
    each is scored with BM25 using document frequencies from
    knowledge_term_stats.
    """
    rows = conn.execute(_BM25_SQL, {"q": query, "k": k}).fetchall()
    return [_hit(r, float(r.bm25), keyword=float(r.bm25)) for r in rows]


def rrf_fuse(*ranked_lists: list[Hit], k: int = RRF_K) -> list[Hit]:
    """Reciprocal rank fusion: score = sum over lists of 1 / (k + rank).

    Works on ranks only, so cosine similarities and ts_rank values never have
    to be put on one scale.
    """
    fused: dict[int, Hit] = {}
    for hits in ranked_lists:
        for rank, h in enumerate(hits, 1):
            entry = fused.get(h.chunk_id)
            if entry is None:
                entry = fused[h.chunk_id] = Hit(
                    h.chunk_id, h.doc_slug, h.doc_title, h.heading, h.content, h.source, 0.0, {}
                )
            entry.score += 1.0 / (k + rank)
            entry.scores.update(h.scores)
    return sorted(fused.values(), key=lambda h: h.score, reverse=True)


def hybrid_search(conn: Connection, query: str, k: int = CANDIDATES, query_vector=None) -> list[Hit]:
    return rrf_fuse(
        vector_search(conn, query, k, query_vector=query_vector),
        keyword_search(conn, query, k),
    )[:k]


_reranker = None


def get_reranker():
    global _reranker
    if _reranker is None:
        # pyrefly: ignore [missing-import]
        from sentence_transformers import CrossEncoder

        _reranker = CrossEncoder(RERANK_MODEL_NAME)
    return _reranker


def rerank(query: str, hits: list[Hit]) -> list[Hit]:
    """Re-scores candidates with a cross-encoder, which reads query and chunk
    together instead of comparing two independently computed vectors."""
    if not hits:
        return hits
    scores = get_reranker().predict(
        [(query, f"{h.doc_title} — {h.heading or ''}\n{h.content}") for h in hits]
    )
    for h, s in zip(hits, scores):
        h.scores["rerank"] = float(s)
        h.score = float(s)
    return sorted(hits, key=lambda h: h.score, reverse=True)


MODES = ("vector", "keyword", "hybrid", "rerank")


def search(conn: Connection, query: str, mode: str = "hybrid", top_k: int = 5, query_vector=None) -> list[Hit]:
    if mode == "vector":
        hits = vector_search(conn, query, top_k, query_vector=query_vector)
    elif mode == "keyword":
        hits = keyword_search(conn, query, top_k)
    elif mode == "hybrid":
        hits = hybrid_search(conn, query, query_vector=query_vector)
    elif mode == "rerank":
        hits = rerank(query, vector_search(conn, query, RERANK_CANDIDATES, query_vector=query_vector))
    else:
        raise ValueError(f"unknown search mode: {mode}")
    return hits[:top_k]


def timed_search(conn: Connection, query: str, mode: str, top_k: int = 5) -> tuple[list[Hit], float]:
    start = time.perf_counter()
    hits = search(conn, query, mode, top_k)
    return hits, (time.perf_counter() - start) * 1000
