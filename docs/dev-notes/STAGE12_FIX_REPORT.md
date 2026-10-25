# Stage 12 Fix Report: Context-Aware Classification and Routing

## Problem Statement

**Bug Discovered:** Implicit order references in multi-turn conversations were being misrouted to RAG instead of tool execution.

**Example Scenario:**
```
Turn 1: "Where is order 1008?"  → ✅ Routed to tool execution
Turn 2: "Can I cancel it?"      → ✅ Routed to tool execution
Turn 3: "Actually, check order 1011." → ✅ Context switched to order 1011
Turn 4: "What's the status?"    → ❌ Misclassified as "General Inquiry", routed to RAG
```

**Root Cause:** Classification operated only on the current message without leveraging persistent conversation context (active_order_id, recent messages, last_action).

## Solution Overview

Fixed classification and routing to be context-aware while maintaining security boundaries:
1. **Enhanced classification** to receive conversation context
2. **Updated LLM prompt** to interpret contextual follow-ups correctly
3. **Added routing safeguards** to override misclassification when appropriate
4. **Preserved authorization** - backend still validates all operations

## Changes Made

### 1. Enhanced `classify_ticket()` in `app/llm.py`

**Before:**
```python
def classify_ticket(message: str) -> dict:
    # Only used current message
    classification = classify_ticket(state["customer_message"])
```

**After:**
```python
def classify_ticket(
    message: str,
    active_order_id: int | None = None,
    active_ticket_id: int | None = None,
    last_action: str | None = None,
    recent_messages: list[dict] | None = None,
) -> dict:
    # Enriches system prompt with conversation context
    system_prompt += """
    Recent conversation:
    User: Where is order 1011?
    Assistant: Order 1011 has been delivered.
    
    Active order context: Order #1011
    Last action: get_order_status
    
    IMPORTANT: If the current message is an implicit follow-up related to 
    the active order (e.g., 'What's the status?', 'Can I cancel it?'), 
    classify it as 'Order Issue', not 'General Inquiry'.
    """
```

**Key Points:**
- Passes last 3 messages for context (limited to avoid token bloat)
- Includes active_order_id hint for implicit reference resolution
- Explicit instruction to classify implicit follow-ups correctly
- Maintains existing output schema (category, priority, sentiment)

### 2. Updated `classify_node()` in `app/graph.py`

**Before:**
```python
def classify_node(state: SupportFlowState) -> dict:
    classification = classify_ticket(state["customer_message"])
```

**After:**
```python
def classify_node(state: SupportFlowState) -> dict:
    classification = classify_ticket(
        message=state["customer_message"],
        active_order_id=state.get("active_order_id"),
        active_ticket_id=state.get("active_ticket_id"),
        last_action=state.get("last_action"),
        recent_messages=state.get("conversation_history"),
    )
```

### 3. Enhanced `route_after_classify()` with Safeguards

**Added routing safeguard logic:**

```python
def route_after_classify(state: SupportFlowState) -> str:
    # ... existing routing ...
    
    # ROUTING SAFEGUARD: Override misclassification for implicit order actions
    active_order_id = state.get("active_order_id")
    message_lower = state.get("customer_message", "").lower()
    
    ORDER_ACTION_KEYWORDS = [
        "status", "cancel", "refund", "replace", "track", "ship",
        "deliver", "return", "exchange", "modify", "update", "change"
    ]
    
    POLICY_KEYWORDS = ["policy", "policies", "rule", "rules", "guideline"]
    
    if active_order_id and "general inquiry" in category_lower:
        is_policy_question = any(kw in message_lower for kw in POLICY_KEYWORDS)
        
        if not is_policy_question:
            has_order_action = any(kw in message_lower for kw in ORDER_ACTION_KEYWORDS)
            is_short_contextual = len(message_lower.split()) <= 5 and has_order_action
            
            if has_order_action or is_short_contextual:
                return "tool_execution_node"  # Override to tools
    
    # Standard routing continues...
```

**Safeguard Logic:**
- **Triggers:** Only when `active_order_id` exists AND classified as "General Inquiry"
- **Detects:** Order action keywords (status, cancel, refund, etc.)
- **Excludes:** Policy questions (maintains RAG routing for "What's your return policy?")
- **Preserves:** Explicit classification for most cases (only overrides edge cases)

**Examples:**

| Message | Active Order | Classification | Safeguard? | Route |
|---------|--------------|----------------|------------|-------|
| "What's the status?" | 1011 | General Inquiry | ✅ Override | Tool Execution |
| "Can I cancel it?" | 1008 | General Inquiry | ✅ Override | Tool Execution |
| "What is your return policy?" | 1011 | General Inquiry | ❌ Policy question | RAG |
| "What's the status?" | None | General Inquiry | ❌ No context | RAG |
| "Where is order 1020?" | 1011 | Order Issue | ❌ Correct classification | Tool Execution |

## Security Guarantees Maintained

✅ **Authorization unchanged** - Backend still validates customer ownership
✅ **Context not trusted** - active_order_id guides LLM but backend validates
✅ **Cross-customer isolation** - Unauthorized orders fail at authorization layer
✅ **Idempotency preserved** - Context updates only on first execution
✅ **No context injection** - Customer cannot manipulate active_order_id via API

## Test Coverage

### New Tests Added (`test_context_aware_classification.py`)

1. ✅ **test_context_aware_classification_implicit_status_query** (LLM)
   - "What's the status?" with active_order_id routes to tool execution
   
2. ✅ **test_context_switch_explicit_order_mention** (LLM)
   - "Actually, check order 1011" → "What's the status?" uses new context
   
3. ✅ **test_implicit_cancel_request_with_context** (LLM)
   - "Can I cancel it?" with active order context routes to tools
   
4. ✅ **test_routing_safeguard_override**
   - Safeguard overrides "General Inquiry" when order action keywords present
   
5. ✅ **test_routing_without_context_respects_classification**
   - Without context, ambiguous messages route to RAG safely
   
6. ✅ **test_routing_policy_question_always_goes_to_rag**
   - Policy questions route to RAG even with order context
   
7. ✅ **test_routing_explicit_order_id_overrides_context**
   - Explicit order ID overrides active context
   
8. ✅ **test_customer_isolation_with_context** (LLM)
   - Cross-customer access blocked by authorization layer
   
9. ✅ **test_no_context_ambiguous_message_safe_behavior**
   - Ambiguous messages without context have safe default routing

### Test Results

```bash
# Non-LLM tests (routing logic)
pytest tests/test_context_aware_classification.py -m "not llm"
Result: 5 passed ✅

# All non-LLM tests (regression check)
pytest -m "not llm"
Result: 230 passed ✅

# LLM tests (requires GROQ_API_KEY)
pytest tests/test_context_aware_classification.py -m "llm"
Result: To be run with API access
```

## Files Modified

1. **app/llm.py**
   - Enhanced `classify_ticket()` to accept conversation context parameters
   - Updated system prompt to include context hints

2. **app/graph.py**
   - Updated `classify_node()` to pass context to classifier
   - Enhanced `route_after_classify()` with safeguard logic

3. **tests/test_context_aware_classification.py** (NEW)
   - Comprehensive test coverage for context-aware routing
   - Both LLM and non-LLM tests

## Backward Compatibility

✅ **Existing calls preserved** - `classify_ticket()` parameters are optional (default None)
✅ **No API changes** - FastAPI endpoints unchanged
✅ **No DB migrations** - Uses existing conversation context schema
✅ **All existing tests pass** - 230/230 tests passing

## Routing Decision Tree

```
User Message arrives
    ↓
classify_node (with context)
    ↓
┌─────────────────────────────────────────┐
│ Classification Result                    │
│ {category, priority, sentiment}         │
└──────────────┬──────────────────────────┘
               ↓
        route_after_classify
               ↓
    ┌──────────┴──────────┐
    ↓                     ↓
Knowledge Category    Action Category
    │                     │
    ↓                     ↓
    │              ┌──────────────┐
    │              │ Has active   │
    │              │ order_id?    │
    │              └──┬───────────┘
    │                 │
    │          ┌──────┴───────┐
    │          ↓              ↓
    │         Yes            No
    │          │              │
    │  ┌───────┴────┐         │
    │  ↓            ↓         │
    │ Policy?   Order Action? │
    │  │            │         │
    │  ↓            ↓         │
    │ Yes → RAG   Yes → Tools │
    │              │          │
    │              No → Standard
    │                   Classification
    ↓                        ↓
   RAG                   Tools / RAG
```

## Verification Checklist

- [✅] Classification receives conversation context
- [✅] Routing safeguard overrides misclassification appropriately
- [✅] Policy questions still route to RAG
- [✅] Authorization layer unchanged
- [✅] Customer isolation maintained
- [✅] Idempotency preserved
- [✅] All existing tests pass
- [✅] New test coverage added
- [ ] Live frontend/API test completed
- [ ] LLM tests with GROQ_API_KEY completed

## Next Steps

1. **Run LLM tests** with GROQ_API_KEY:
   ```bash
   export GROQ_API_KEY=your_key
   pytest tests/test_context_aware_classification.py -m "llm" -xvs
   ```

2. **Live verification** via frontend:
   - Start backend: `uvicorn app.main:app --reload`
   - Start frontend: `cd frontend && npm run dev`
   - Test conversation sequence:
     1. "Where is order 1008?"
     2. "Can I cancel it?"
     3. "Actually, check order 1011."
     4. "What's the status?"
   - Verify: Final request calls `get_order_status(1011)`

3. **Monitor in production**:
   - Track classification accuracy via OpenTelemetry
   - Monitor safeguard trigger rate
   - Watch for false positives (policy questions routed to tools)

## Summary

**Problem:** Implicit order references misrouted to RAG  
**Solution:** Context-aware classification + routing safeguards  git diff --stat
**Impact:** ✅ Improved multi-turn conversation handling  
**Risk:** ✅ Minimal - all existing tests pass, new tests added  
**Security:** ✅ Unchanged - authorization layer still validates all operations
