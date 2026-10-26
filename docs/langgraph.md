# SupportFlow LangGraph workflow

Generated from `app/graph.py` by `python scripts/export_graph.py`; do not edit by hand.

- Solid arrows always run; dashed arrows are conditional routes (`route_after_classify`, `route_after_tool`).
- Orange nodes pause the run with `interrupt()`. State is checkpointed in Postgres (thread = conversation), and an admin's approve/reject resumes it.

## Router mode (default, `ROUTING_MODE=router`)

A classifier labels the message and the router sends it to RAG or the tool loop.

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	classify_node(classify_node)
	retrieve_knowledge_node(retrieve_knowledge_node)
	tool_execution_node(tool_execution_node)
	approval_check_node(approval_check_node)
	generate_response_node(generate_response_node)
	error_node(error_node)
	__end__([<p>__end__</p>]):::last
	__start__ --> classify_node;
	approval_check_node -.-> generate_response_node;
	classify_node -.-> error_node;
	classify_node -.-> retrieve_knowledge_node;
	classify_node -.-> tool_execution_node;
	tool_execution_node -.-> approval_check_node;
	tool_execution_node -.-> error_node;
	tool_execution_node -.-> generate_response_node;
	error_node --> __end__;
	generate_response_node --> __end__;
	retrieve_knowledge_node --> __end__;
	tool_execution_node -.-> tool_execution_node;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
	classDef interrupt fill:#ffe3c2,stroke:#c46a00,stroke-width:2px
	class approval_check_node interrupt
```

## Hybrid mode (`ROUTING_MODE=hybrid`)

The router's graph, unchanged: pure policy questions still take the cheap RAG path. The difference is inside `tool_execution_node`, which also gets the read-only `search_policy` tool, so a question that needs an order and a rule can read both.

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	classify_node(classify_node)
	retrieve_knowledge_node(retrieve_knowledge_node)
	tool_execution_node(tool_execution_node)
	approval_check_node(approval_check_node)
	generate_response_node(generate_response_node)
	error_node(error_node)
	__end__([<p>__end__</p>]):::last
	__start__ --> classify_node;
	approval_check_node -.-> generate_response_node;
	classify_node -.-> error_node;
	classify_node -.-> retrieve_knowledge_node;
	classify_node -.-> tool_execution_node;
	tool_execution_node -.-> approval_check_node;
	tool_execution_node -.-> error_node;
	tool_execution_node -.-> generate_response_node;
	error_node --> __end__;
	generate_response_node --> __end__;
	retrieve_knowledge_node --> __end__;
	tool_execution_node -.-> tool_execution_node;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
	classDef interrupt fill:#ffe3c2,stroke:#c46a00,stroke-width:2px
	class approval_check_node interrupt
```

## Agent mode (`ROUTING_MODE=agent`)

No classify step: the tool loop has a read-only `search_policy` tool and decides whether to read policy, call order tools, or both.

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	tool_execution_node(tool_execution_node)
	approval_check_node(approval_check_node)
	generate_response_node(generate_response_node)
	error_node(error_node)
	__end__([<p>__end__</p>]):::last
	__start__ --> tool_execution_node;
	approval_check_node -.-> generate_response_node;
	tool_execution_node -.-> approval_check_node;
	tool_execution_node -.-> error_node;
	tool_execution_node -.-> generate_response_node;
	error_node --> __end__;
	generate_response_node --> __end__;
	tool_execution_node -.-> tool_execution_node;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
	classDef interrupt fill:#ffe3c2,stroke:#c46a00,stroke-width:2px
	class approval_check_node interrupt
```
