# list_customer_orders Routing Fix - Report

## STATUS: ✅ COMPLETED

**Issue**: "Show me my orders" was being classified as "General Inquiry" and routed to RAG instead of the `list_customer_orders` tool.

---

## 1. ROOT CAUSE

### Classification Problem
The LLM classifier categorizes messages into predefined categories:
- **"General Inquiry"** = asking how something works, policy, feedback, NO specific action on an order
- **"Order Issue"** = problem with a specific order needing action  
- **"Refund Request"** = asking for money back
- **Billing"** = payment/charges
- **"Technical Support"** = app/website problems

**"Show me my orders"** doesn't fit any action category - it's not an "issue", not a "refund", not "billing". It's asking to VIEW/LIST orders, which the classifier interprets as "General Inquiry".

### Routing Problem
The LangGraph routing function (`route_after_classify`) routes "General Inquiry" to RAG by default. The existing Stage 12 safeguard only handles implicit order references when an `active_order_id` exists, but "Show me my orders" has no active order context - it's asking for the entire collection.

### Why Tool Was Available But Never Called
- `list_customer_orders` IS properly registered in `AVAILABLE_TOOLS`
- Tool schema IS exposed to the LLM
- BUT: LangGraph routes based on classification FIRST
- If routed to RAG, tools are never even considered

---

## 2. SOLUTION IMPLEMENTED

### A. Improved Tool Description (app/llm.py)

**Before:**
```python
"description": "Lists orders for the authenticated customer with optional status filter."
```

**After:**
```python
"description": (
    "Lists/shows all orders belonging to the authenticated customer. "
    "Use this tool when the customer wants to VIEW, LIST, or SEE their own orders. "
    "The backend automatically filters to show only the authenticated customer's orders. "
    "Examples: 'Show me my orders', 'List my orders', 'What orders do I have?', "
    "'Can I see my orders?', 'Show my recent orders'. "
    "This is a read-only operation that returns order summaries (order_id, status, total, created_at)."
),
```

**Why**: Makes it crystal clear to the LLM WHEN to use this tool, with explicit examples.

### B. Added Routing Safeguard (app/graph.py)

Added a narrow, deterministic routing safeguard in `route_after_classify()` that detects order-list requests even when misclassified as "General Inquiry".

**Detection patterns:**
- "show me my order"
- "show my order"
- "list my order"
- "see my order"
- "view my order"
- "what orders do i have"
- "what orders have i"
- "do i have any order"
- "can i see my order"
- "display my order"
- "get my order"
- "my recent order"
- "my past order"

**Exclusions (stay with RAG):**
- Policy questions: "policy", "policies", "rule", "rules", "guideline", "guidelines"
- Info questions: "how long", "how does"

**Logic:**
```python
if "general inquiry" in category_lower:
    is_policy_question = any(keyword in message_lower for keyword in POLICY_KEYWORDS)
    
    if not is_policy_question:
        is_order_list_request = any(pattern in message_lower for pattern in ORDER_LIST_PATTERNS)
        
        if is_order_list_request:
            # Route to tool execution
            return "tool_execution_node"
```

### C. Preserved Stage 12 Behavior

The new order-list safeguard is **separate from** the existing Stage 12 contextual order routing. Both coexist:

1. **New safeguard**: Collection-level order list requests (no active_order_id needed)
   - "Show me my orders" → `list_customer_orders()`

2. **Stage 12 safeguard**: Implicit references to active specific order (requires active_order_id)
   - "Actually, check order 1011" → active_order_id=1011
   - "What's the status?" → `get_order_status(1011)`

---

## 3. FILES CHANGED

### Modified Files (2):
1. **app/llm.py** - Enhanced `LIST_CUSTOMER_ORDERS_TOOL_SCHEMA` description
2. **app/graph.py** - Added order-list routing safeguard in `route_after_classify()`

### New Tests (1):
3. **tests/test_graph.py** - Added `TestOrderListRequestRouting` class with 9 tests

---

## 4. ROUTING CHANGE DETAILS

### Before Fix:
```
User: "Show me my orders"
↓
classify_ticket() → {"category": "General Inquiry", ...}
↓
route_after_classify() → "retrieve_knowledge_node" (RAG)
↓
answer_with_knowledge_base() → "I'm sorry, but..."
```

### After Fix:
```
User: "Show me my orders"
↓
classify_ticket() → {"category": "General Inquiry", ...}
↓
route_after_classify() → [ORDER-LIST SAFEGUARD TRIGGERED]
↓
"tool_execution_node"
↓
LLM sees list_customer_orders tool with clear description
↓
list_customer_orders() executed
↓
Returns actual customer orders from PostgreSQL
```

---

## 5. TESTS ADDED

### New Test Class: `TestOrderListRequestRouting` (9 tests)

**Order-list requests that SHOULD route to tools:**
1. ✅ `test_show_me_my_orders_routes_to_tools` - "Show me my orders"
2. ✅ `test_list_my_orders_routes_to_tools` - "List my orders"
3. ✅ `test_what_orders_do_i_have_routes_to_tools` - "What orders do I have?"
4. ✅ `test_can_i_see_my_orders_routes_to_tools` - "Can I see my orders?"
5. ✅ `test_show_my_recent_orders_routes_to_tools` - "Show my recent orders"

**Policy/info questions that SHOULD stay with RAG:**
6. ✅ `test_policy_question_stays_with_rag` - "What is your return policy?"
7. ✅ `test_shipping_info_question_stays_with_rag` - "How long does shipping take?"

**Stage 12 compatibility:**
8. ✅ `test_stage12_contextual_routing_still_works` - "What's the status?" with active_order_id=1011
9. ✅ `test_order_list_request_distinct_from_specific_order` - "List my orders" with active_order_id

---

## 6. TEST RESULTS

### Before Fix:
- **281 deterministic tests** passing

### After Fix:
- **290 deterministic tests** passing (+9 new routing tests)
- **0 regressions**
- All existing tests continue to pass

```bash
$ pytest -m "not llm" -q
290 passed, 13 deselected in 60.37s
```

---

## 7. VERIFICATION SCENARIOS

### A. "Show me my orders" - PRIMARY FIX
**Expected:**
- Route to `tool_execution_node`
- LLM calls `list_customer_orders()`
- Backend returns authenticated customer's orders only
- Final response lists the actual orders

**Result:** ✅ Routes correctly via safeguard

### B. "List my recent orders"
**Expected:** Same as A
**Result:** ✅ Routes correctly

### C. "What is your return policy?"
**Expected:**
- Route to `retrieve_knowledge_node` (RAG)
- NOT to list_customer_orders tool

**Result:** ✅ Policy safeguard works, stays with RAG

### D. "How long does shipping take?"
**Expected:** Route to RAG (info question)
**Result:** ✅ Stays with RAG

### E. Stage 12 Contextual - "What's the status?" (with active_order_id=1011)
**Expected:**
- Route to `tool_execution_node` via Stage 12 safeguard
- LLM calls `get_order_status(1011)`

**Result:** ✅ Stage 12 behavior preserved

### F. "List my orders" (with active_order_id=1011)
**Expected:**
- Route to `tool_execution_node`
- LLM calls `list_customer_orders()` (collection-level, ignores active order)

**Result:** ✅ Works correctly - order-list safeguard independent of active order

---

## 8. STAGE 12 COMPATIBILITY CONFIRMATION

✅ **Confirmed**: Existing Stage 12 contextual routing behavior is fully preserved.

**Stage 12 Examples Still Working:**
1. "Actually, check order 1011" → sets active_order_id=1011
2. "What's the status?" → `get_order_status(1011)` via Stage 12 safeguard
3. "Can I cancel it?" → routes to tools with active order context

**New Order-List Examples:**
1. "Show me my orders" → `list_customer_orders()` via new safeguard
2. "List my orders" → works independently (no active order needed)

**No Conflicts**: The two safeguards check different conditions and don't interfere.

---

## 9. MCP STATUS

✅ **Confirmed**: MCP was NOT implemented. This fix is purely routing logic.

---

## 10. ARCHITECTURE PRESERVATION

✅ **No architectural changes:**
- LangGraph orchestration unchanged
- Classification categories unchanged
- Tool dispatch unchanged
- Database schema unchanged
- Authorization model unchanged
- Customer isolation unchanged

✅ **Only changes:**
- Tool description improved (documentation)
- Routing logic enhanced (narrow safeguard added)
- Tests added (verification)

---

## 11. SECURITY VERIFICATION

✅ **Backend identity security preserved:**
- Customer identity STILL derived from JWT auth
- `list_customer_orders()` automatically filters by authenticated customer
- LLM CANNOT supply customer_id for authorization
- Cross-customer access blocked by backend

✅ **Authorization flow unchanged:**
```
JWT → authenticated_customer_id → list_customer_orders() → 
    PostgreSQL query WHERE customer_id = authenticated_customer_id
```

---

## 12. REMAINING WORK

This fix addresses ONLY the routing problem for `list_customer_orders`.

**Completed:**
- ✅ Tool description enhanced
- ✅ Routing safeguard added
- ✅ Tests added
- ✅ All tests passing
- ✅ Stage 12 compatibility verified

**NOT in scope for this fix:**
- Tool hardening (separate task)
- Response grounding (separate task)
- MCP integration (future)
- New tools (not needed for this fix)

---

## SUMMARY

**Problem**: "Show me my orders" routed to RAG instead of `list_customer_orders` tool.

**Root Cause**: Classification returned "General Inquiry" which routes to RAG by default. Tool was available but never reached.

**Solution**: 
1. Enhanced tool description with clear examples
2. Added narrow routing safeguard for order-list requests
3. Preserved all existing behavior (Stage 12, RAG, policy routing)

**Impact**:
- ✅ 290 tests passing (+9 new tests)
- ✅ 0 regressions
- ✅ Stage 12 behavior preserved
- ✅ No architectural changes
- ✅ Security model unchanged

**Status**: Fix complete and verified.
