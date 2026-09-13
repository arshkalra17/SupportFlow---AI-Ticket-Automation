# BANKING77 Mapping Experiment - Summary Report

**Experiment Date:** 2026-09-13  
**Stage 9B Baseline:** 50.0% accuracy (77/154 correct)  
**Hypothesis:** Current low accuracy is primarily caused by taxonomy mapping errors, not classifier failure

---

## Executive Summary

A systematic audit of all 77 BANKING77 → SupportFlow category mappings revealed **16 likely incorrect mappings (20.8%)** affecting **32 test cases** (20.8%).

**Root cause:** Banking transaction-state problems (pending transfers, declined payments, failed transactions, balance posting issues) were incorrectly mapped to "Order Issue" (e-commerce fulfillment) instead of "Billing" (payment/transaction problems).

**Key finding:** "Order Issue" had 2.6% accuracy (1/38 cases) in Stage 9B baseline. This category contained 21 intents, but 14 of them (67%) are actually payment/transaction problems that semantically belong in "Billing."

---

## Audit Results

### Mappings Reviewed: 77 intents

| Assessment | Count | Percentage |
|------------|-------|------------|
| ✅ **Clearly Correct** | 56 | 72.7% |
| ❌ **Likely Incorrect** | 16 | 20.8% |
| ⚠️ **Ambiguous** | 5 | 6.5% |

### Changes Proposed: 16 intents

| Change Type | Count | Cases Affected |
|-------------|-------|----------------|
| Order Issue → Billing | 13 | 26 |
| Order Issue → Technical Support | 2 | 4 |
| Order Issue → Billing (ambiguous) | 1 | 2 |
| **Total** | **16** | **32** |

---

## Category Distribution Changes

### Current (Stage 9B Baseline)

| Category | Intents | Test Cases | Baseline Accuracy |
|----------|---------|------------|-------------------|
| General Inquiry | 27 | 54 | 81.5% (44/54) |
| **Order Issue** | **21** | **42** | **2.6% (1/38)** ⚠️ |
| Billing | 16 | 32 | 72.7% (16/22) |
| Technical Support | 10 | 20 | 40.6% (13/32) |
| Refund Request | 3 | 6 | 37.5% (3/8) |

### Proposed (Experiment)

| Category | Intents | Test Cases | Change |
|----------|---------|------------|--------|
| General Inquiry | 27 | 54 | No change |
| **Order Issue** | **4** | **8** | **-16 intents** ⬇️ |
| **Billing** | **30** | **60** | **+13 intents** ⬆️ |
| **Technical Support** | **13** | **26** | **+3 intents** ⬆️ |
| Refund Request | 3 | 6 | No change |

**Impact:** Order Issue reduced from 27.3% to 5.2% of dataset. Billing increased from 20.8% to 39.0%.

---

## Top 10 Changes (by Impact)

All 13 transaction-state problems moved from Order Issue → Billing:

| Rank | Intent | Current | Proposed | Cases |
|------|--------|---------|----------|-------|
| 1 | pending_transfer | Order Issue | **Billing** | 2 |
| 2 | pending_card_payment | Order Issue | **Billing** | 2 |
| 3 | declined_card_payment | Order Issue | **Billing** | 2 |
| 4 | failed_transfer | Order Issue | **Billing** | 2 |
| 5 | balance_not_updated_after_bank_transfer | Order Issue | **Billing** | 2 |
| 6 | pending_cash_withdrawal | Order Issue | **Billing** | 2 |
| 7 | pending_top_up | Order Issue | **Billing** | 2 |
| 8 | top_up_failed | Order Issue | **Billing** | 2 |
| 9 | declined_cash_withdrawal | Order Issue | **Billing** | 2 |
| 10 | balance_not_updated_after_cheque_or_cash_deposit | Order Issue | **Billing** | 2 |

**Additional changes:**
- card_payment_not_recognised (Order Issue → Billing): 2 cases
- cash_withdrawal_not_recognised (Order Issue → Billing): 2 cases
- declined_transfer (Order Issue → Billing): 2 cases
- card_swallowed (Order Issue → Technical Support): 2 cases
- beneficiary_not_allowed (Order Issue → Technical Support): 2 cases
- wrong_amount_of_cash_received (Order Issue → Billing, ambiguous): 2 cases

---

## Semantic Justification

### The Core Taxonomy Mismatch

**E-commerce support (SupportFlow's domain):**
- **Order Issue** = physical fulfillment problem (wrong item, package delayed, tracking issue)
- **Billing** = payment/transaction/charge problem (declined, unexpected fee, wrong amount)

**Banking (BANKING77's domain):**
- Transactions ARE the core product
- A "pending transfer" is a transaction-state problem, not an order delivery problem

### The Pattern: Transaction State ≠ Order Fulfillment

Current (incorrect) logic mapped banking transactions to Order Issue:
- "pending_transfer" → Order Issue (transaction not complete)
- "declined_card_payment" → Order Issue (transaction failed)
- "balance_not_updated" → Order Issue (transaction not showing)

**Problem:** These are payment/transaction **processing** issues, not physical **delivery** issues.

**Correct mapping:**
- Payment status (pending/declined/failed) → **Billing**
- Unrecognized charge → **Billing**
- Balance posting problem → **Billing**
- Physical card delivery → **Order Issue** ✅

---

## Validation: Remaining Order Issues (4 intents)

After corrections, only genuine order/delivery problems remain:

| Intent | Category | Justification |
|--------|----------|---------------|
| **card_arrival** | Order Issue | Physical item delivery problem ✅ |
| **cancel_transfer** | Order Issue | Action on specific transaction (defensible) |
| **transfer_not_received_by_recipient** | Order Issue | Delivery failure (ambiguous but defensible) |
| **wrong_amount_of_cash_received** | Billing (changed) | Amount discrepancy = billing issue |

---

## Expected Experimental Outcome

### Hypothesis

If the classifier correctly identifies banking transaction problems as **Billing** (which is semantically correct), it currently gets them "wrong" because the gold labels map them to "Order Issue."

### Prediction

With corrected mappings:
1. **Overall accuracy increases** (currently 50.0%)
2. **Order Issue accuracy remains low** (smaller category, more ambiguous cases)
3. **Billing accuracy increases significantly** (transaction problems correctly labeled)
4. **General Inquiry accuracy remains high** (already 81.5%, no changes)

### Test Cases Affected: 32 (20.8% of dataset)

If classifier correctly predicted "Billing" for these 32 cases:
- Current baseline: counted as **wrong** (gold label = "Order Issue")
- Experimental: would be counted as **correct** (gold label = "Billing")

**Maximum possible accuracy improvement:** +20.8 percentage points (if all 32 were correctly classified as Billing)

**Realistic improvement:** Depends on how many of the 32 the classifier already predicted as Billing in Stage 9B

---

## Semantic Rule Established

**Independent semantic principle (not accuracy-driven):**

Banking transaction intents should map to SupportFlow categories as follows:

| Banking Transaction Type | SupportFlow Category | Examples |
|--------------------------|----------------------|----------|
| **Information/How-To** | General Inquiry | "How do I transfer?", "What currencies are supported?" |
| **Transaction Status** | Billing | "Transfer is pending", "Payment declined", "Balance not updated" |
| **Unexpected Charge** | Billing | "Unrecognized payment", "Fee charged", "Wrong amount" |
| **Physical Delivery** | Order Issue | "Card hasn't arrived", "Card stuck in ATM" |
| **Account Access** | Technical Support | "Can't log in", "Identity verification failed", "PIN blocked" |
| **Money Back** | Refund Request | "Request refund", "Charge reversed" |

This rule is **consistent with e-commerce support semantics** and does not require knowledge of classifier performance.

---

## Files Created

1. **MAPPING_AUDIT_REPORT.md** - Complete 77-intent audit with detailed rationale
2. **banking77_mapping_experiment.json** - Experimental mapping file with proposed changes
3. **EXPERIMENT_SUMMARY.md** - This summary report

---

## Production Code Status ✅

**Confirmed unchanged:**
- ✅ `app/llm.py` (classifier implementation)
- ✅ `evaluation/config.py` (prompt text, version v1-a0ce2844)
- ✅ `evaluation/datasets/banking77_benchmark.json` (154 test cases)
- ✅ `evaluation/datasets/banking77_mapping.json` (current gold labels)
- ✅ All production business logic
- ✅ Stage 9B baseline results preserved

**Files created (experiment only):**
- `evaluation/datasets/MAPPING_AUDIT_REPORT.md`
- `evaluation/datasets/banking77_mapping_experiment.json`
- `evaluation/datasets/EXPERIMENT_SUMMARY.md`

---

## Next Steps

### Phase 1: Review
1. Validate semantic reasoning for proposed changes
2. Check that changes are justified independently of accuracy impact
3. Confirm no changes made to improve accuracy artificially

### Phase 2: Test
1. Modify `evaluation/runners/banking77.py` to support experimental mapping file
2. Run classifier against experimental mappings (use existing Stage 9B cache)
3. Compare results to Stage 9B baseline

### Phase 3: Analyze
1. Calculate accuracy change
2. Determine how much improvement comes from mapping corrections vs classifier errors
3. Report findings

---

## Critical Experiment Rule ✅

**Confirmed:**
- ❌ No live Groq runs performed
- ❌ No evaluation executed
- ❌ No production mappings modified
- ❌ No classifier changes
- ❌ No prompt changes
- ❌ No Stage 9B results overwritten
- ✅ All changes isolated to experimental files
- ✅ Baseline preserved for comparison

**STOPPED as instructed. Ready for review.**

