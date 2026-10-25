# Retrieval scale check

116 labelled questions; synthetic product-page distractors added next to the real section chunks · pgvector HNSW (m=16, ef_construction=64) · 2026-10-25 22:47 UTC

| Chunks | Exact Hit@1 (vector / rerank) | HNSW ef=40 Hit@1 (vector / rerank) | HNSW ef=200 Hit@1 (vector / rerank) | Recall@10 ef=40 / ef=200 | p50 ms exact / ef=40 / ef=200 | Scoped HNSW Hit@1 (vector / rerank) | Scoped p50 ms |
|---|---|---|---|---|---|---|---|
| 120 | 83.6 / 87.9 | 83.6 / 87.9 | 83.6 / 87.9 | 100.0 / 100.0 | 1.7 / 1.7 / 1.1 | 83.6 / 87.9 | 1.2 |
| 1,120 | 81.9 / 84.5 | 75.0 / 77.6 | 81.9 / 84.5 | 95.3 / 100.0 | 4.5 / 1.2 / 4.8 | 83.6 / 87.9 | 1.4 |
| 10,120 | 81.0 / 83.6 | 32.8 / 35.3 | 81.0 / 83.6 | 62.9 / 100.0 | 37.0 / 1.5 / 36.6 | 83.6 / 87.9 | 1.5 |
| 50,120 | 81.0 / 83.6 | 12.1 / 14.7 | 21.6 / 24.1 | 37.3 / 55.0 | 79.3 / 2.3 / 3.1 | 83.6 / 87.9 | 0.9 |
