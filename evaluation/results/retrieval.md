# Knowledge base retrieval: what was measured and what was kept

The original knowledge base had 4 two-sentence documents, each embedded whole,
and was searched with pgvector cosine similarity. Its tests reported 100%
Hit@1, which mostly says that 4 short documents are easy to tell apart. This
step grew the knowledge base to a realistic size, built a labelled question
set, and tried chunking, hybrid search and re-ranking. Each one was kept only
if it improved the numbers.

## Setup

- **Corpus.** `knowledge_base/`: 24 markdown articles, 120 `##` sections
  (returns, shipping, payments, gift cards, warranty, account security and so
  on). The four original policies keep their titles and their rules, which the
  backend enforces: 30-day refunds, admin approval above $1000, cancellation
  only in PROCESSING. Several articles deliberately overlap (damaged items vs
  replacements, international vs domestic shipping, preorder price guarantee
  vs price drops), so some questions have a plausible wrong answer nearby.
- **Questions.** `evaluation/datasets/retrieval_v1.json`: 116 customer-style
  questions, each labelled with the article and section that answers it,
  tagged as `paraphrase` (75), `confusable` (22) or `lexical` (19). There
  are also 20 out-of-scope questions: 12 obviously off-topic and 8 near misses
  that sound shop-related, such as "do you offer installation services".
- **Dev/test split.** Even ids are dev and odd ids are test. Configurations
  were chosen on dev; test is a check that the choice was not fitted to
  every question.
- **Metrics.** Hit@k counts the right article in the top k (chunks
  de-duplicated to articles), plus MRR@5. Ctx@3 counts the right article
  among the first 3 chunks, which is what the answering LLM sees. Sec@k uses
  the exact section. The OOS AUROC and best-threshold refusal and answer
  rates measure how well the top score separates in-scope from out-of-scope
  questions. "vs ref" counts per-question Hit@1 wins and losses against
  section-level vector search.
- **Harness.** `python -m evaluation.retrieval_eval` (no LLM calls) writes
  `retrieval_latest.md`/`.json`. The pipeline under test is
  `app/retrieval.py`, the same code the app runs.

## Results

| Config | Hit@1 | Hit@3 | MRR@5 | Ctx@3 | Sec@1 | Dev Hit@1 | Test Hit@1 | vs ref | OOS AUROC | p50 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| Whole-article vector (baseline) | 74.1 | 92.2 | 0.835 | 92.2 | — | 70.7 | 77.6 | +8 / −19 | 0.965 | 15 |
| **Section chunks, vector** | 83.6 | 97.4 | 0.899 | 94.8 | 80.2 | 81.0 | 86.2 | — | **0.990** | 16 |
| Section chunks, BM25 keyword | 56.9 | 83.6 | 0.699 | 81.9 | 44.0 | 56.9 | 56.9 | +6 / −37 | 0.915 | 1 |
| Section chunks, hybrid (RRF) | 79.3 | 91.4 | 0.861 | 91.4 | 68.1 | 75.9 | 82.8 | +4 / −9 | 0.988 | 20 |
| **Section chunks, vector → bge re-rank** | **87.9** | **98.3** | **0.928** | 96.6 | **81.9** | **87.9** | **87.9** | **+10 / −5** | 0.956 | 183 |

### 1. Chunking: kept

Splitting articles at `##` headings raised Hit@1 from 74.1 to 83.6. That held
on both halves (dev +10.3, test +8.6) and came out 19 questions won against 8
lost. A whole-article embedding averages five topics together, and MiniLM
truncates input at 256 tokens, so later sections of long articles barely
register. Each chunk is embedded as "article title — section heading" plus
its text, so a short section keeps its context.

### 2. Hybrid search: measured, not kept

Postgres' built-in `ts_rank_cd` has no IDF, so the word "order" counted as
much as "bitcoin" (keyword Hit@1 was 55.2). It was replaced with BM25
computed in SQL. The GIN index finds candidates, and document frequencies come
from a `knowledge_term_stats` materialized view. That improved keyword search
on its own (Hit@3 went from 75.9 to 83.6), but fusing it with vector search
still lost. Equal-weight RRF scored 79.3, and every keyword weight tried on
dev (0.1–1.0) did worse than vector alone. On this corpus the vocabulary
rarely lines up with how customers phrase things ("bitcoin" vs
"cryptocurrency", "get out of it" vs "cancel"). Exact-term matches are where
keyword search helps, and section-level vectors already handle those. BM25 and
RRF stay in `app/retrieval.py` as measured modes. Hybrid search is the
first thing to re-test once the knowledge base holds SKUs, error codes or
product names.

### 3. Re-ranking: kept

`cross-encoder/ms-marco-MiniLM-L-6-v2` was rejected. It gained on test and
lost on dev, which is noise rather than signal; it is trained on web search
queries, not support questions. `BAAI/bge-reranker-base`, re-scoring the
vector top 10, improved both halves: dev 81.0 → 87.9, test 86.2 → 87.9, and
+10 / −5 questions overall. A top 10 re-rank beat top 5 and top 20 on dev.
Lexical questions went to 100% and paraphrases to 89.3%. Confusable questions
dropped from 81.8 to 72.7, which is where a stronger model or more context per
chunk would help next. The cost is about 170 ms more per query on CPU and a
~1 GB model, small next to the LLM call that follows. `RAG_RERANK=0` turns it
off.

### 4. Abstaining on out-of-scope questions

The old threshold of 0.15 cosine similarity refused almost nothing; even a
cake recipe scores above it. On this set the best cut is **0.30**. That
refuses 95% of out-of-scope questions, including near misses, while still
answering 99% of in-scope ones. Cosine similarity is a better
answer-or-refuse signal than the re-ranker score (AUROC 0.990 vs 0.956). So
the app thresholds on similarity first and only then re-ranks what passed.

### Also tried: `bge-small-en-v1.5` embeddings

Same 384 dimensions, so it drops straight into the existing schema. Article
Hit@1 rose (86.2 vs 83.6) but section Hit@1 fell (77.6 vs 80.2). With the
re-ranker on top, it was no better than MiniLM. The result was mixed, so it
was not adopted.

## Scale

`python -m evaluation.retrieval_scale` adds up to 50,000 synthetic product-page
chunks (about 400× the real corpus) next to the 120 real sections. The
product pages share the corpus vocabulary: batteries, shipping, warranty,
orders. The script then measures Hit@1 and latency for exact search, for HNSW,
and for HNSW over a partial index scoped to policy content.
Full table: [`retrieval_scale.md`](retrieval_scale.md).

Hit@1 is shown as vector / vector + re-rank.

| Chunks | Exact search | Shared HNSW, ef_search 40 | Shared HNSW, ef_search 200 | Scoped HNSW (partial index) | p50 ms exact / shared ef=40 / scoped |
|---|---|---|---|---|---|
| 120 | 83.6 / 87.9 | 83.6 / 87.9 | 83.6 / 87.9 | 83.6 / 87.9 | 1.7 / 1.7 / 1.2 |
| 1,120 | 81.9 / 84.5 | 75.0 / 77.6 | 81.9 / 84.5 | 83.6 / 87.9 | 4.5 / 1.2 / 1.4 |
| 10,120 | 81.0 / 83.6 | 32.8 / 35.3 | 81.0 / 83.6 | 83.6 / 87.9 | 37.0 / 1.5 / 1.5 |
| 50,120 | 81.0 / 83.6 | 12.1 / 14.7 | 21.6 / 24.1 | 83.6 / 87.9 | 79.3 / 2.3 / 0.9 |

At 10k chunks, ef_search 200 matches exact search on both accuracy and
latency (about 37 ms); at 50k it falls apart again.

**More content barely hurts ranking.** With exact search, Hit@1 only goes
from 83.6 to 81.0 at 50k chunks. The queries' best real section averages
0.57 cosine similarity, against 0.34 for the best distractor.

**One shared HNSW graph does hurt.** At pgvector's default settings, recall@10
fell to about 40% at 50k chunks and Hit@1 collapsed. The cause is how the graph
is connected, not how many candidates it checks. The 120 policy sections form
a small cluster far from the product pages: mean cosine 0.08 between the two
groups, against about 0.22 within each. Greedy graph search starting in the big
cluster rarely crosses over. Every usual tuning setting was tried at 50k
chunks:

| Change | Recall@10 | Hit@1 | p50 ms |
|---|---|---|---|
| Default (m=16, ef_construction=64), ef_search 40 / 100 / 200 / 400 | 44 / 52 / 58 / 66 | 16 / 22 / 25 / 33 | 2–6 |
| ef_search 1000 | 99.9 | 81.0 | 76 (as slow as exact) |
| Index built after the bulk load instead of during inserts | 39–56 | 13–23 | 2–3 |
| pgvector 0.8 `iterative_scan = relaxed_order` | unchanged | unchanged | 2 |
| Denser graph m=32, ef_construction=200 (6× build time) | 42–53 | 12–22 | 3–6 |
| Denser graph m=48, ef_construction=400 (11× build time) | 49–58 | 20–30 | 3–4 |
| **Partial HNSW index on policy content, query scoped to it** | — | **83.6** | **0.9** |

**Takeaway for the design.** When the knowledge base grows by adding
different kinds of content, such as product pages, help articles or past
tickets, give each kind its own partial index, or its own table, and scope the
query to it. Don't put everything in one HNSW graph and tune `ef_search`.
The app needs neither today: with 120 chunks Postgres scans the table in
about 1 ms and the planner skips the index. The HNSW index is in the
migration for when the table grows, and the scoped-index pattern is the
next step once a second kind of content arrives.

Caveat: the distractors are template-generated, so they are more uniform
than real product pages. The cluster effect is real, but its size on real data
would need re-measuring.

## Pipeline now in the app

`app/kb.py` → `search_knowledge_base()`:

1. Embed the query with all-MiniLM-L6-v2 and take the 10 nearest sections
   from `knowledge_chunks` (pgvector, HNSW index).
2. Drop sections below 0.30 cosine similarity. If none are left, the RAG node
   refuses instead of answering.
3. Re-rank the rest with `BAAI/bge-reranker-base` and hand the top 3 sections
   (title, heading, text) to the LLM.

The migrate service indexes `knowledge_base/*.md` on first start. After
editing an article, run `seed_knowledge_base(force=True)`.

## Golden-set check

Retrieval alone, with no LLM, on the 23 golden cases that expect a policy
answer: the labelled article is in the LLM's context for 22 and on top for 18.
The one miss is policy-015 ("If my order already shipped, how do I get out of
it?"). MiniLM does not connect "get out of it" with cancellation, and the
cancellation rules are not in the top 10. The corpus was not edited to rescue
it, because that would fit the knowledge base to the test. Two of the cases
where it isn't on top retrieve the new damaged-items article ahead of the
replacement policy for "arrived damaged" questions. That article answers them
and names the replacement policy.

## Limitations

- The same author wrote the corpus and the questions. Questions were
  written in customer voice with different vocabulary from the articles, but
  real traffic would be the better test. Logged production queries labelled
  by an agent are the next dataset.
- 116 questions means one question moves Hit@1 by 0.9 points (1.7 on a single
  split). Differences under about 3 points are within noise, which is why
  each decision also looks at both splits and per-question wins and losses.
- The 20 out-of-scope questions are enough to pick a threshold, not to
  estimate a precise refusal rate.
