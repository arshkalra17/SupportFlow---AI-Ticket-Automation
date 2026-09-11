# SupportFlow AI Evaluation

Run timestamp : 2026-09-11T22:30:23.278081+00:00
Model         : openai/gpt-oss-120b

```
================================================
SupportFlow AI Evaluation
================================================

Classification
  Dataset size        : 20 cases
  Evaluated           : 20
  Category accuracy   : 80.0%
  Priority accuracy   : 65.0%
  Sentiment accuracy  : 60.0%
  Exact-match accuracy: 30.0%

Tool Selection
  Dataset size        : 15 cases
  Evaluated           : 15
  Sequence accuracy   : 100.0%
  First-tool accuracy : 100.0%

RAG Retrieval
  In-domain cases     : 20
  Out-of-domain cases : 5
  Hit@1               : 100.0%
  Hit@3               : 100.0%
  OOD rejection rate  : 100.0%

Security (Authorization)
  Dataset size        : 20 cases
  BLOCK cases         : 11
  ALLOW cases         : 8
  Blocking rate       : 100.0%
  Allow accuracy      : 100.0%
  Overall accuracy    : 100.0%

Approval Routing
  Dataset size        : 20 cases
  Routing accuracy    : 100.0%
    AUTO_EXECUTE      : 8/8 (100.0%)
    HUMAN_APPROVAL    : 6/6 (100.0%)
    REJECT            : 6/6 (100.0%)

LLM calls used
  classification        : 0
  tool_selection        : 0
  rag                   : 0
  authorization         : 0
  approval_routing      : 0
  total                 : 0
================================================
```
