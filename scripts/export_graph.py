"""
Writes docs/langgraph.md: the SupportFlow LangGraph workflow as a Mermaid
diagram, generated from the compiled graph so it can't drift from the code
(tests/test_graph.py fails if the committed file is out of date).

Usage:
    python scripts/export_graph.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "docs" / "langgraph.md"

# Nodes that pause the run with interrupt() until a human decides
INTERRUPT_NODES = ["approval_check_node"]


def _mermaid(mode: str) -> str:
    from app.graph import build_supportflow_graph

    mermaid = build_supportflow_graph(mode=mode).get_graph().draw_mermaid().rstrip()
    mermaid += "\n\tclassDef interrupt fill:#ffe3c2,stroke:#c46a00,stroke-width:2px"
    for node in INTERRUPT_NODES:
        mermaid += f"\n\tclass {node} interrupt"
    return mermaid


def render() -> str:
    return (
        "# SupportFlow LangGraph workflow\n\n"
        "Generated from `app/graph.py` by `python scripts/export_graph.py`; "
        "do not edit by hand.\n\n"
        "- Solid arrows always run; dashed arrows are conditional routes "
        "(`route_after_classify`, `route_after_tool`).\n"
        "- Orange nodes pause the run with `interrupt()`. State is "
        "checkpointed in Postgres (thread = conversation), and an admin's "
        "approve/reject resumes it.\n\n"
        "## Router mode (default, `ROUTING_MODE=router`)\n\n"
        "A classifier labels the message and the router sends it to RAG or the tool loop.\n\n"
        f"```mermaid\n{_mermaid('router')}\n```\n\n"
        "## Hybrid mode (`ROUTING_MODE=hybrid`)\n\n"
        "The router's graph, unchanged: pure policy questions still take the cheap RAG path. "
        "The difference is inside `tool_execution_node`, which also gets the read-only "
        "`search_policy` tool, so a question that needs an order and a rule can read both.\n\n"
        f"```mermaid\n{_mermaid('hybrid')}\n```\n\n"
        "## Agent mode (`ROUTING_MODE=agent`)\n\n"
        "No classify step: the tool loop has a read-only `search_policy` tool and "
        "decides whether to read policy, call order tools, or both.\n\n"
        f"```mermaid\n{_mermaid('agent')}\n```\n"
    )


def main() -> int:
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("OTEL_SDK_DISABLED", "true")
    OUTPUT.write_text(render(), encoding="utf-8")
    print(f"Wrote {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
