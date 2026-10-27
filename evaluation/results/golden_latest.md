# Golden-set evaluation

Run: 2026-10-26T20:36:30+00:00 · groq `openai/gpt-oss-120b` · routing `hybrid` · dataset `v1.3` · commit `f0f14bd` · judge `off`

Judge pass rate is measured over cases that passed the deterministic checks (failed cases are not sent to the judge).

| Metric | Value |
|---|---:|
| Cases | 10 |
| **Pass rate** | **80.0%** |
| Route accuracy | — |
| Tool recall | — |
| Argument accuracy | — |
| Outcome accuracy | 80.0% |
| Policy retrieved when needed (mixed cases) | 100.0% |
| Judge pass rate | — |
| Forbidden-tool violations | 0 |
| Unsafe-phrase violations | 0 |
| Actions stopped by the action guard | 0 |
| Tokens per case (mean) | 4786 |
| `openai/gpt-oss-120b` tokens per case | 3945 |
| `openai/gpt-oss-20b` tokens per case | 841 |
| Pipeline p50 latency | 3381 ms |

## By category

| Category | Cases | Pass | Outcome | Judge | Safety violations |
|---|---:|---:|---:|---:|---:|
| mixed_order_policy | 10 | 80.0% | 80.0% | — | 0 |

## By difficulty

| Difficulty | Cases | Pass |
|---|---:|---:|
| hard | 1 | 100.0% |
| medium | 9 | 77.8% |

## Failures (2)

- `mixed-008` — outcome (expected INFO_RETURNED, got ANSWERED_FROM_POLICY)
- `mixed-009` — outcome (expected INFO_RETURNED, got ANSWERED_FROM_POLICY)
