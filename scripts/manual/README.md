# Manual scripts

Ad-hoc scripts used while building each stage. They are not part of the
pytest suite (which lives in `tests/`), and most of them call the live
Groq API, so they need `GROQ_API_KEY` and a running PostgreSQL + Redis.

Run from the repository root:

```bash
PYTHONPATH=. python scripts/manual/stage6_graph_demo.py
```

| Script | What it exercises |
|---|---|
| `stage6_graph_demo.py` | End-to-end LangGraph flows (RAG, tools, approval) |
| `stage6_refund_demo.py` | High-value refund routed to human approval |
| `stage7d_retry_checks.py` | Failure recovery, retries and idempotent replay (mocked, no Groq) |
| `context_flow_demo.py` | Multi-turn context: implicit order references across turns |
