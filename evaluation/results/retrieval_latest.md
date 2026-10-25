# Retrieval eval

Dataset `retrieval-v1` · 116 questions (58 dev / 58 test) + 20 out-of-scope · corpus 24 articles · 2026-10-25 22:48 UTC

Percentages are over all questions; dev and test columns show Hit@1 on each half.

`vs ref` counts questions won / lost at Hit@1 against `section:vector`.

| Config | Chunks | Hit@1 | Hit@3 | MRR@5 | Ctx@3 | Sec@1 | Sec@3 | Dev Hit@1 | Test Hit@1 | vs ref | OOS AUROC | Abstain (refused / answered) | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `document:vector` | 24 | 74.1 | 92.2 | 0.835 | 92.2 | — | — | 70.7 | 77.6 | +8 / −19 | 0.965 | 90.0 / 93.1 @ 0.283 | 14 | 18 |
| `section:vector` | 120 | 83.6 | 97.4 | 0.899 | 94.8 | 80.2 | 90.5 | 81.0 | 86.2 | — | 0.990 | 95.0 / 99.1 @ 0.300 | 16 | 18 |
| `section:keyword` | 120 | 56.9 | 83.6 | 0.699 | 81.9 | 44.0 | 69.0 | 56.9 | 56.9 | +6 / −37 | 0.915 | 90.0 / 80.2 @ 5.479 | 1 | 4 |
| `section:hybrid` | 120 | 79.3 | 91.4 | 0.861 | 91.4 | 68.1 | 84.5 | 75.9 | 82.8 | +4 / −9 | 0.988 | 95.0 / 98.3 @ 0.300 | 19 | 24 |
| `section:rerank` | 120 | 87.9 | 98.3 | 0.928 | 96.6 | 81.9 | 93.1 | 87.9 | 87.9 | +10 / −5 | 0.956 | 85.0 / 96.6 @ 0.002 | 182 | 242 |

## Hit@1 by question type

| Config | confusable (n=22) | lexical (n=19) | paraphrase (n=75) |
|---|---|---|---|
| `document:vector` | 68.2 | 100.0 | 69.3 |
| `section:vector` | 81.8 | 89.5 | 82.7 |
| `section:keyword` | 40.9 | 73.7 | 57.3 |
| `section:hybrid` | 68.2 | 84.2 | 81.3 |
| `section:rerank` | 72.7 | 100.0 | 89.3 |
