# SupportFlow design

This document explains the decisions behind SupportFlow, what was measured to
make them, and what did not work. The README covers what the system does; this
covers why it is built this way. Every number below comes from a file in
`evaluation/results/` or a test in `tests/`, and each section names its source.

## 1. Problem and constraints

A customer-support assistant has to answer policy questions, look things up in
an order system, and sometimes move money (refunds, cancellations,
replacements). The model is good at language and unreliable at being
accountable. The design question is where to put the boundary so that a wrong
model output cannot cause a wrong action.

Constraints that shaped the work:

- **Free-tier models.** Groq's free tier gives `gpt-oss-120b` 200K tokens per
  day (rolling 24 hours) and 8K per minute, per organisation. A full 167-case
  evaluation costs about 430K tokens, so it cannot run in one day. That made
  cost per case a design input, not an afterthought.
- **Actions are not reversible.** A refund that executes twice is a real loss,
  so execution has to be exactly-once and re-validated at execution time.
- **Nothing the model says is trusted.** Prompt injection is assumed.

## 2. Architecture in one paragraph

A FastAPI service authenticates the caller with a JWT and passes a LangGraph
workflow the trusted `customer_id`. A classifier labels the message; the router
sends it either to retrieval (policy questions) or to a tool loop (orders,
tickets, refunds). Tool calls are proposals: each passes argument validation,
an ownership check, an action guard and business rules before anything runs.
High-risk actions pause the graph with `interrupt()`, the state is checkpointed
in Postgres, and an admin's decision resumes the same run. Postgres is the
source of truth; Redis only carries background ticket work. The graph itself is
in [langgraph.md](langgraph.md).

## 3. Decisions, with the evidence

### 3.1 The model proposes, the backend decides

Every tool call goes through four independent gates: Pydantic validation,
ownership authorization, deterministic business rules, then execution. The
model is never asked whether something is allowed. An early version asked it to
judge refund eligibility and it sometimes got that wrong, so the rule now lives
in code and the prompt only repeats it.

Approved actions run through the same executor as automatic ones
(`app/actions.py`), which re-checks ownership, the order total and prior
refunds under a row lock at execution time. If the world changed while the
request waited for approval, the approval is recorded and the action is marked
`FAILED` instead of executing.

### 3.2 An action guard in front of state-changing tools

The first 167-case run found that the model sometimes acted on a *question*
(asking whether something is possible got a real cancellation or refund call),
and that a message with a negative refund amount produced a refund call. The
prompt already said not to.

`app/action_guard.py` is a deterministic pre-execution check: a refund,
cancellation or replacement is blocked if the message is phrased as a question
with no request phrase, and a negative amount is rejected outright. The tool
result tells the model the action was not executed, so it can ask the customer
to confirm.

| | Forbidden-tool violations | Unsafe phrases |
|---|---:|---:|
| 167-case run before the guard (router, dataset v1.2) | 2 | 1 |
| Same saved run, re-scored on v1.3 with the guard replayed | 0 | 0 |
| Every smoke run since (router, agent, hybrid; live) | 0 | 0 |

The second row is an offline replay of saved model outputs, not a new live run,
and is labelled that way wherever it is quoted. Source:
`evaluation/results/golden_baseline.json`, `tests/test_action_guard.py`.

### 3.3 Retrieval: measure every addition, keep only what wins

The original knowledge base was four short documents, and its tests reported
100% Hit@1, which only shows that four documents are easy to tell apart. The
knowledge base became 24 articles (120 sections), with 116 labelled questions
including deliberately confusable ones and 20 out-of-scope ones.

| Configuration | Hit@1 | Kept? |
|---|---:|---|
| Whole-article vector search (original) | 74.1 | baseline |
| Section chunks, vector | 83.6 | yes (+9.5; held on both dev and test halves) |
| Section chunks, BM25 keyword only | 56.9 | no |
| Section chunks, hybrid (RRF) | 79.3 | **no, worse than vector alone** |
| Section chunks, vector then `bge-reranker-base` | **87.9** | yes (Hit@3 98.3) |

Hybrid search lost at every keyword weight tried on the dev split, because
customers do not use the corpus vocabulary ("get out of it" versus "cancel").
It stays in the code as a measured mode, to re-test once the knowledge base
holds SKUs or error codes. A second re-ranker (`ms-marco-MiniLM`) gained on test
and lost on dev, which is noise, so it was rejected. Full write-up:
[retrieval.md](../evaluation/results/retrieval.md).

**Out-of-scope refusal.** The original 0.15 cosine threshold refused almost
nothing; a cake recipe scored above it. The best cut on this set is 0.30, which
refuses 95% of out-of-scope questions (including near misses such as "do you
offer installation") while answering 99% of in-scope ones. Thresholding on
cosine similarity beat thresholding on the re-ranker score (AUROC 0.990 versus
0.956), so the app checks similarity first and re-ranks only what passed.

### 3.4 Retrieval at scale: one shared HNSW index fails quietly

The scale test adds up to 50,000 synthetic product-page chunks next to the real
sections, using the same vocabulary so the distractors are hard.

| Chunks | Exact search Hit@1 | Shared HNSW, `ef_search` 40 | Shared HNSW, `ef_search` 200 | Scoped partial index |
|---:|---:|---:|---:|---:|
| 120 | 83.6 | 83.6 | 83.6 | 83.6 |
| 1,120 | 81.9 | 75.0 | 81.9 | 83.6 |
| 10,120 | 81.0 | 32.8 | 81.0 | 83.6 |
| 50,120 | 81.0 | **12.1** | **21.6** | **83.6** |

(Vector-only Hit@1. Source: `evaluation/results/retrieval_scale.md`.)

The shared index degrades silently: nothing errors, and answers just get worse
as unrelated content accumulates, and even `ef_search` 200 reaches only 21.6 at
50,000 rows. Scoping the query to a partial index on policy content kept
accuracy flat at 0.9 ms. The conclusion for any retrieval system that will
share a table with other content: give each kind its own partial index or
table and scope the query, instead of tuning one global graph. The current
corpus is small enough (120 rows) that the planner skips the index, so the
HNSW index sits in the migration for when the table grows.

### 3.5 Evaluating the agent: a golden set with a regression gate

167 hand-written cases across 14 categories (policy, lookups, refunds,
cancellations, authorization, prompt injection, multi-turn, out-of-scope,
hallucination traps, and mixed order-plus-policy questions). Each case states
the expected route, tools, arguments, outcome, and the phrases the answer must
never contain. Scoring is deterministic first (route, tools, arguments, outcome,
forbidden tools, unsafe phrases), with an LLM judge only for the free-text
quality of cases that already passed the deterministic checks.

- **Smoke gate.** A fixed, stratified 26-case subset is compared against a
  committed baseline in CI. Because one case is 3.8 points, the gate tolerates
  one flaky case on a set this small; a larger drop, or any new forbidden-tool
  call, fails the build.
- **Resumable.** The daily token cap means a full run cannot finish in a day,
  so progress is saved per case and `--resume` skips finished work. A daily-quota
  error exits with a distinct code, so a wrapper can wait and retry.
- **Dataset changes are versioned.** Eight cases were loosened in v1.3 to accept
  read-only tools that give an equivalent answer, and refusals that move no
  money. Each carries a written reason, and any before-and-after is quoted with
  the dataset version beside it. v1.4 fixed a scoring false positive rather
  than a case: `halluc-001` banned the phrase "tracking number is", which also
  appears in the *correct* answer "the tracking number is not provided", so two
  modes were failed for answering correctly. Banned phrases can now be regexes
  (`re:` prefix), and this one matches only a tracking-number-shaped code.

First full run (router mode, dataset v1.2): **82.6%** (138/167), route accuracy
100%, tool recall 94.4%, 10 mixed order-and-policy cases all failing because the
router can only choose one path. Excluding those 10, 87.9%. Re-scored offline on
v1.3 with the guard replayed: 148/167 (88.6%), 0 forbidden-tool violations.

### 3.6 Three routing modes, measured

The router sends each message down one path, so it cannot answer "can I cancel
order 7003, and when would I get the refund?", which needs the order *and* the
policy. Two alternatives were built and compared (`ROUTING_MODE`):

- `router` (default): classify, then retrieval or the tool loop.
- `agent`: no classifier; one tool loop with a `search_policy` tool.
- `hybrid`: the router's graph, with `search_policy` also available inside the
  tool loop. The hypothesis was that it would keep the router's cheap path for
  pure policy questions and still handle mixed ones.

Same 26-case smoke set, all three scored on dataset v1.4 from saved
observations (no new model calls):

| | Router | Agent | Hybrid |
|---|---:|---:|---:|
| Smoke pass rate | 25/26 | 24/26 | 25/26 |
| Failed case(s) | mixed-001 | refund-001, refund-002 | refund-001 |
| Mixed order-and-policy (10 cases) | 0/10 | 8/10 | 8/10 |
| `gpt-oss-120b` tokens per case | 1,908 | 2,690 | 2,905 |
| `gpt-oss-20b` tokens per case | 887 | 0 | 948 |
| p50 latency | 4.2 s | 6.9 s | 2.5 s |
| p95 latency (second-slowest of 26) | 20.3 s | 18.4 s | 17.0 s |
| Forbidden-tool violations | 0 | 0 | 0 |

**What this showed.** On single-intent questions the three modes are within one
case of each other, and the router's only failure is a mixed question it
cannot answer by design, so the extra machinery buys nothing there. Only mixed questions separate the
modes, and agent and hybrid both reach 8/10. **The hypothesis was wrong:**
hybrid is not cheaper than agent, it spends the most on the model whose daily
quota is the limit. The likely cause is the larger tool list sent on its tools
path, which was not isolated with a separate measurement. Its latency
advantage is the one clear win, but it was measured across different times and
keys, so it is noisy.

**Decision.** `router` stays the default: lowest cost on the binding quota, tied
accuracy on everything except mixed questions. If mixed questions matter,
`agent` is the cheaper way to cover them. `hybrid` stays in the repository as a
measured negative result. Its two mixed failures (`mixed-008`, `mixed-009`) are
not routing errors: the classifier labelled both "Order Issue" and the tool
loop ran, but the model called only `search_policy` and answered from policy
without looking up the order the customer named. Agent mode fails the same way
on `mixed-009`. The likely fix is a prompt rule to look up any order the
customer names before answering; it is untested, because checking it needs live
model calls.

Caveats: 26 and 10 cases are small samples, so differences of one case are not
significant; the router and agent baselines were recorded on dataset v1.2 and
re-scored here, and the hybrid run was recorded on v1.3. The v1.4 change moved
the router and hybrid by one case each (`halluc-001`), so which mode scores
highest on this set is not evidence of anything.

## 4. Reliability

- **Idempotency.** State-changing calls accept an `Idempotency-Key`. A
  `UNIQUE(customer_id, idempotency_key)` constraint plus a SHA-256 payload hash
  gives replay on a retry and `409` on a reused key with a different payload.
  Ten concurrent identical requests produce one execution. A race found along
  the way (a losing thread read another thread's still-pending record) was fixed
  by catching `IntegrityError` on the insert instead of check-then-insert.
- **Resumable approvals.** Approvals are graph interrupts checkpointed in
  Postgres, with the thread id equal to the conversation. Approving or rejecting
  resumes the same run, so the answer the customer sees comes from the same
  state that raised the approval.
- **Failure states.** Tickets end in explicit states (`PROCESSED`,
  `CLASSIFICATION_FAILED`, `QUEUE_FAILED`). Transient errors are retried;
  permanent ones are not retried forever.
- **Rate limiting.** Two Redis limits, both answering `429` with a `Retry-After`
  computed from the window arithmetic, not a fixed guess (tests check that a
  retry at the stated time succeeds and one a moment earlier does not):
  a per-customer request limit (`RATE_LIMIT_PER_MINUTE`, default 10) and a
  shared model-token budget sized to the free Groq tier (7,000 per minute and
  180,000 per day against the 8,000 and 200,000 caps). Real token usage from
  every response, API and worker alike, feeds the budget through a listener on
  the LLM client. Once spent, requests are refused before any model call
  instead of failing at Groq, where each failure costs retries and trips the
  circuit breaker for everyone. Choices: a sliding-window *counter* (current
  bucket plus the previous one weighted by overlap) rather than a fixed window,
  which would allow twice the limit across a boundary, and rather than a
  sorted-set log, which costs memory per request and needs Lua for atomicity;
  it assumes traffic is spread evenly inside a bucket. A refused request is
  taken back out of the counter so hammering does not push the retry time out.
  The limiter **fails open** if Redis is unreachable, matching the telemetry
  layer: the cost is no enforcement during an outage. The budget check runs
  before the call using usage already recorded, so one request can overshoot by
  one call. Anonymous ticket creation is limited by client address, which is
  the proxy's address until a proxy is configured to forward the real one.
- **A failure after a side effect.** Failure injection (`tests/test_resilience.py`)
  found two holes where the model dies *after* a refund, cancellation or
  replacement already went through. (1) The customer got a bare "something
  went wrong" about an action that had happened, which invites a retry. The
  failure reply is now built from the backend's own results ("Refund of $50.00
  issued... check your order before asking again"). (2) The idempotency layer
  read the error in the result as transient and marked the key retryable, so a
  retry with the same key re-ran the whole pipeline, which for a refund under
  the approval threshold could pay out a second time. A run that failed after
  a state change is now recorded as completed and replayed; a run that failed
  before any change stays retryable. Approvals were already safe: the
  "submitted for approval" reply is built without a second model call.
- **Worker job loss.** A database error while processing a ticket returned
  without re-queueing, so the job was gone and the ticket stayed `PENDING`
  forever. The job is now re-queued up to three times, then parked on a
  dead-letter list (`supportflow:ticket_queue:dead`) for inspection. Transient
  model failures back off (1 s, 2 s, 4 s, capped at 30 s) instead of using all
  three retries within milliseconds of each other.
- **Cold start.** The embedding and re-ranking models load on first use: the
  first retrieval in a process took 11.8 s against about 0.1 s afterwards. The
  API now runs one throwaway search at startup (`WARM_UP_MODELS`).
- **Policy-answer cache** (`ANSWER_CACHE_ENABLED=1`, off by default so evals
  measure the model). A repeated question over the same retrieved policy text
  is answered from Redis without a model call, saving about 450 `gpt-oss-120b`
  tokens and a round trip per hit. The key hashes the normalised question, the
  retrieved context and the prompt version, so editing a policy article or the
  prompt changes the key and stale policy cannot be served; there is no
  invalidation step to forget. Exact repeats only: semantic matching was left
  out because a near-duplicate question can have a different right answer.
  Retrieval still runs every time, so out-of-scope questions are still refused
  and never cached. The effect on a real workload is not measured here, only
  the mechanism.
- **Provider fallback.** Groq first, Gemini as fallback, with a per-provider
  circuit breaker and a longer cooldown for daily-quota errors than for
  per-minute limits.
- **CI deadlock found and fixed.** The test suite hung in CI but not locally.
  LangGraph's `PostgresSaver.setup()` runs `CREATE INDEX CONCURRENTLY`, which
  waits for every open transaction in the database, and a test's own session was
  holding one on a fresh database. Reproduced in a `python:3.12` Linux container
  on an empty database, fixed by creating the checkpoint tables once before any
  test session opens, and job timeouts were added so a hang cannot occupy a
  runner for hours.

## 5. Security model

Identity enters the system one way: a verified JWT, loaded to a customer row.
The request schema forbids extra fields, so a client sending its own
`authenticated_customer_id` gets a `422` rather than a silent drop. A prompt
that claims another identity changes nothing, because the model has no channel
to the identity value. The golden set carries 14 authorization and 16
prompt-injection cases, with 0 forbidden-tool violations in every live run since
the guard was added.

## 6. What this does not do yet

- **Rate limiting has gaps.** Limits are per customer and per shared model
  budget only: there is no per-customer *token* cap, so one customer can use
  the whole shared budget until it is spent, and the background worker is not
  throttled by the budget (queued work is the buffer). When the Groq budget is
  spent the API answers 429 instead of falling back to Gemini.
- **Small evaluation samples for the routing comparison.** Twenty-six cases
  cannot separate modes that differ by one case. A full 167-case run per mode
  would cost about 430K to 650K tokens each, so it needs several quota windows.
- **Open failures.** `refund-001` ("Refund $50 for order 7004", total $39.50)
  fails in hybrid and agent mode. The case expects the model to call
  `issue_refund` so the backend rejects it; these modes read the order first and
  decline in words, which moves no money. That is a decision about what the test
  is for (the backend rule is also covered by unit tests), not a model fix.
  The 8,423 tokens `halluc-001` used in hybrid mode were four model calls with a
  large prompt, not a runaway loop.
- **Latency tails are unexplained.** The p95 of the 26-case runs is 17 to 20 s
  in every mode, about five times the median, and p95 of 26 samples is just the
  second-slowest case. The cold start accounts for only part of it (router p95
  falls from 20.3 s to 16.1 s without the first request, and the agent and hybrid
  tails do not move). A two-call policy case that took 20 s used the same calls
  and tokens as ones that took under 3 s, so the time was spent waiting, not
  working. The likely cause is free-tier rate-limit waits, since the eval sends
  cases back to back, but that was not isolated. There is also no load test, so
  no throughput number.
- **Order lookup skipped on mixed questions.** In agent and hybrid mode the
  model sometimes answers an order-plus-policy question from policy alone
  (`mixed-008`, `mixed-009`, and `mixed-003` in agent mode).
- **Test infrastructure.** The test suite runs on one shared database name, so
  two concurrent test runs on one machine can block each other.

## 7. Reproducing the numbers

```bash
python -m evaluation.retrieval_eval            # retrieval table (no LLM calls)
python -m evaluation.retrieval_scale           # scale table (no LLM calls)
python -m evaluation.golden_eval --smoke --routing-mode router --resume
python -m evaluation.golden_eval --smoke --routing-mode agent  --resume
python -m evaluation.golden_eval --smoke --routing-mode hybrid --resume
python -m evaluation.golden_eval --category mixed_order_policy --routing-mode hybrid --no-judge
```

The golden eval needs a live Groq key and roughly 100K tokens for a smoke run
in agent or hybrid mode, which fits in one day's quota on a single key.

Method note: the hybrid smoke run stopped at case 19 when the daily quota ran
out, and cases 20 to 26 and the 10 mixed-question cases were then run through a
local multi-key setup that is not part of this repository. The model, prompts
and dataset were identical, so outputs are not affected by which key served
them, but it was not a single-key run. The router and agent baselines were
recorded on one key.
