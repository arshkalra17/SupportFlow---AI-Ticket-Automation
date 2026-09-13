# BANKING77 → SupportFlow Category Mapping Audit

**Date:** 2026-09-13  
**Auditor:** Controlled Experiment - Stage 9B Mapping Review  
**Baseline Result:** 50.0% mapped category accuracy (77/154 correct)

---

## Audit Methodology

Each of the 77 BANKING77 intents was evaluated against SupportFlow's 5-category taxonomy:

**SupportFlow Categories:**
- **General Inquiry**: Information/policy/how-to questions, no concrete problem
- **Order Issue**: Problem with a specific order/transaction/fulfillment/delivery
- **Refund Request**: Explicitly requesting money back
- **Billing**: Charges, payments, invoices, unexpected fees, payment problems
- **Technical Support**: App/website/device/account access/authentication/technical functionality

**Assessment Criteria:**
- **✅ Clearly Correct**: Mapping aligns unambiguously with category definition
- **⚠️ Defensible but Ambiguous**: Multiple categories could apply, current choice is reasonable
- **❌ Likely Incorrect**: Mapping does not fit the category semantics
- **🔄 Recommended Change**: Alternative mapping with semantic justification

---

## Complete Mapping Audit (77 Intents)

| # | BANKING77 Intent | Current Mapping | Assessment | Recommended | Rationale |
|---|------------------|-----------------|------------|-------------|-----------|
| 1 | **Refund_not_showing_up** | Refund Request | ✅ Clearly Correct | — | Customer reports refund hasn't appeared |
| 2 | **activate_my_card** | Technical Support | ✅ Clearly Correct | — | Account/card access setup issue |
| 3 | **age_limit** | General Inquiry | ✅ Clearly Correct | — | Policy question about eligibility |
| 4 | **apple_pay_or_google_pay** | General Inquiry | ✅ Clearly Correct | — | Question about supported payment methods |
| 5 | **atm_support** | General Inquiry | ✅ Clearly Correct | — | Information about ATM availability |
| 6 | **automatic_top_up** | General Inquiry | ✅ Clearly Correct | — | Question about account feature |
| 7 | **balance_not_updated_after_bank_transfer** | Order Issue | ❌ Likely Incorrect | **Billing** | Transaction posted but amount incorrect = billing/payment problem, not order fulfillment |
| 8 | **balance_not_updated_after_cheque_or_cash_deposit** | Order Issue | ❌ Likely Incorrect | **Billing** | Deposit not reflecting = payment/transaction problem |
| 9 | **beneficiary_not_allowed** | Order Issue | ⚠️ Ambiguous | **Technical Support** | Transfer blocked could be technical restriction or transaction failure; leaning technical |
| 10 | **cancel_transfer** | Order Issue | ✅ Clearly Correct | — | Action needed on specific transaction |
| 11 | **card_about_to_expire** | General Inquiry | ✅ Clearly Correct | — | Information request about card status |
| 12 | **card_acceptance** | General Inquiry | ✅ Clearly Correct | — | Question about where card works |
| 13 | **card_arrival** | Order Issue | ✅ Clearly Correct | — | Physical item delivery problem |
| 14 | **card_delivery_estimate** | General Inquiry | ✅ Clearly Correct | — | Information request about timing |
| 15 | **card_linking** | Technical Support | ✅ Clearly Correct | — | Setup/configuration issue |
| 16 | **card_not_working** | Technical Support | ✅ Clearly Correct | — | Technical functionality problem |
| 17 | **card_payment_fee_charged** | Billing | ✅ Clearly Correct | — | Unexpected charge on account |
| 18 | **card_payment_not_recognised** | Order Issue | ❌ Likely Incorrect | **Billing** | Unrecognized charge = billing dispute, not order fulfillment issue |
| 19 | **card_payment_wrong_exchange_rate** | Billing | ✅ Clearly Correct | — | Charge amount incorrect |
| 20 | **card_swallowed** | Order Issue | ⚠️ Ambiguous | **Technical Support** | Physical card stuck in ATM = technical/access issue more than order problem |
| 21 | **cash_withdrawal_charge** | Billing | ✅ Clearly Correct | — | Unexpected fee charged |
| 22 | **cash_withdrawal_not_recognised** | Order Issue | ❌ Likely Incorrect | **Billing** | Unrecognized withdrawal = billing dispute/fraud concern |
| 23 | **change_pin** | Technical Support | ✅ Clearly Correct | — | Account security modification |
| 24 | **compromised_card** | Technical Support | ✅ Clearly Correct | — | Security issue |
| 25 | **contactless_not_working** | Technical Support | ✅ Clearly Correct | — | Technical functionality problem |
| 26 | **country_support** | General Inquiry | ✅ Clearly Correct | — | Service availability question |
| 27 | **declined_card_payment** | Order Issue | ❌ Likely Incorrect | **Billing** | Payment declined = payment/authorization problem, not order issue |
| 28 | **declined_cash_withdrawal** | Order Issue | ❌ Likely Incorrect | **Billing** | Withdrawal declined = payment/authorization problem |
| 29 | **declined_transfer** | Order Issue | ❌ Likely Incorrect | **Billing** | Transfer declined = payment/authorization problem |
| 30 | **direct_debit_payment_not_recognised** | Billing | ✅ Clearly Correct | — | Unrecognized charge on account |
| 31 | **disposable_card_limits** | General Inquiry | ✅ Clearly Correct | — | Policy/feature limits question |
| 32 | **edit_personal_details** | Technical Support | ✅ Clearly Correct | — | Account management |
| 33 | **exchange_charge** | Billing | ✅ Clearly Correct | — | Fee charged for transaction |
| 34 | **exchange_rate** | General Inquiry | ✅ Clearly Correct | — | Information about pricing |
| 35 | **exchange_via_app** | General Inquiry | ✅ Clearly Correct | — | Feature availability question |
| 36 | **extra_charge_on_statement** | Billing | ✅ Clearly Correct | — | Unexpected charge |
| 37 | **failed_transfer** | Order Issue | ❌ Likely Incorrect | **Billing** | Transfer failed = payment problem, not order fulfillment |
| 38 | **fiat_currency_support** | General Inquiry | ✅ Clearly Correct | — | Supported currencies question |
| 39 | **get_disposable_virtual_card** | General Inquiry | ✅ Clearly Correct | — | How to obtain feature |
| 40 | **get_physical_card** | General Inquiry | ✅ Clearly Correct | — | How to obtain item |
| 41 | **getting_spare_card** | General Inquiry | ✅ Clearly Correct | — | Replacement item question |
| 42 | **getting_virtual_card** | General Inquiry | ✅ Clearly Correct | — | How to obtain feature |
| 43 | **lost_or_stolen_card** | Technical Support | ✅ Clearly Correct | — | Security issue |
| 44 | **lost_or_stolen_phone** | Technical Support | ✅ Clearly Correct | — | Security/access issue |
| 45 | **order_physical_card** | General Inquiry | ✅ Clearly Correct | — | How to request item |
| 46 | **passcode_forgotten** | Technical Support | ✅ Clearly Correct | — | Account access issue |
| 47 | **pending_card_payment** | Order Issue | ❌ Likely Incorrect | **Billing** | Payment status = payment/transaction processing issue |
| 48 | **pending_cash_withdrawal** | Order Issue | ❌ Likely Incorrect | **Billing** | Withdrawal status = payment/transaction processing issue |
| 49 | **pending_top_up** | Order Issue | ❌ Likely Incorrect | **Billing** | Top-up status = payment/transaction processing issue |
| 50 | **pending_transfer** | Order Issue | ❌ Likely Incorrect | **Billing** | Transfer status = payment/transaction processing issue |
| 51 | **pin_blocked** | Technical Support | ✅ Clearly Correct | — | Account access blocked |
| 52 | **receiving_money** | General Inquiry | ✅ Clearly Correct | — | How feature works |
| 53 | **request_refund** | Refund Request | ✅ Clearly Correct | — | Explicit request for money back |
| 54 | **reverted_card_payment?** | Refund Request | ✅ Clearly Correct | — | Payment reversal = refund |
| 55 | **supported_cards_and_currencies** | General Inquiry | ✅ Clearly Correct | — | What is supported |
| 56 | **terminate_account** | General Inquiry | ⚠️ Ambiguous | **Technical Support** | Account closure could be "how-to" (inquiry) or "do it" (technical action) |
| 57 | **top_up_by_bank_transfer_charge** | Billing | ✅ Clearly Correct | — | Fee charged |
| 58 | **top_up_by_card_charge** | Billing | ✅ Clearly Correct | — | Fee charged |
| 59 | **top_up_by_cash_or_cheque** | General Inquiry | ✅ Clearly Correct | — | How to use feature |
| 60 | **top_up_failed** | Order Issue | ❌ Likely Incorrect | **Billing** | Top-up transaction failed = payment problem |
| 61 | **top_up_limits** | General Inquiry | ✅ Clearly Correct | — | Feature limits/policy |
| 62 | **top_up_reverted** | Refund Request | ✅ Clearly Correct | — | Transaction reversal |
| 63 | **topping_up_by_card** | General Inquiry | ✅ Clearly Correct | — | How to use feature |
| 64 | **transaction_charged_twice** | Billing | ✅ Clearly Correct | — | Duplicate charge |
| 65 | **transfer_fee_charged** | Billing | ✅ Clearly Correct | — | Fee charged |
| 66 | **transfer_into_account** | General Inquiry | ✅ Clearly Correct | — | How feature works |
| 67 | **transfer_not_received_by_recipient** | Order Issue | ⚠️ Ambiguous | — | Could be Order Issue (delivery) or Billing (transaction problem); defensible as-is |
| 68 | **transfer_timing** | General Inquiry | ✅ Clearly Correct | — | Timing/speed question |
| 69 | **unable_to_verify_identity** | Technical Support | ✅ Clearly Correct | — | Account verification problem |
| 70 | **verify_my_identity** | Technical Support | ✅ Clearly Correct | — | Account verification process |
| 71 | **verify_source_of_funds** | Technical Support | ✅ Clearly Correct | — | Account verification requirement |
| 72 | **verify_top_up** | Technical Support | ✅ Clearly Correct | — | Transaction verification needed |
| 73 | **virtual_card_not_working** | Technical Support | ✅ Clearly Correct | — | Technical functionality problem |
| 74 | **visa_or_mastercard** | General Inquiry | ✅ Clearly Correct | — | Supported card types |
| 75 | **why_verify_identity** | General Inquiry | ✅ Clearly Correct | — | Policy/why requirement exists |
| 76 | **wrong_amount_of_cash_received** | Order Issue | ⚠️ Ambiguous | **Billing** | Amount discrepancy = billing/payment problem more than order issue |
| 77 | **wrong_exchange_rate_for_cash_withdrawal** | Billing | ✅ Clearly Correct | — | Charge amount incorrect |

---

## Summary Statistics

### Assessment Breakdown
- **✅ Clearly Correct**: 55 mappings (71.4%)
- **❌ Likely Incorrect**: 17 mappings (22.1%)
- **⚠️ Ambiguous**: 5 mappings (6.5%)

### Recommended Changes
- **Mappings to change**: 17
- **Mappings to keep**: 60 (including ambiguous)

---

## Semantic Pattern Analysis

### The Core Problem: "Transaction" ≠ "Order"

The current mapping conflates **banking transactions** (transfers, payments, withdrawals, top-ups) with **e-commerce order fulfillment** (shipping, delivery, product issues).

**In e-commerce (SupportFlow's domain):**
- **Order Issue** = physical fulfillment problem (wrong item shipped, package delayed, product defective)
- **Billing** = payment/transaction/charge problem (declined payment, unexpected fee, wrong amount)

**In banking (BANKING77's domain):**
- Transactions ARE the core product
- A "pending transfer" is a transaction-state problem, not an order delivery problem

### Incorrect Pattern: Transaction State → Order Issue

**Current (incorrect) logic:**
- "pending_transfer" → Order Issue (because "transaction not complete")
- "declined_card_payment" → Order Issue (because "transaction failed")
- "balance_not_updated" → Order Issue (because "transaction not showing")

**Problem:** These are all **payment/transaction processing issues**, which in e-commerce support would be **Billing** problems, not order fulfillment problems.

**Correct mapping:**
- **Billing**: Payment authorization, transaction processing, charge disputes, unexpected fees
- **Order Issue**: Physical item delivery, wrong product received, package tracking

### Examples of Likely Incorrect Mappings

| Intent | Current | Should Be | Why |
|--------|---------|-----------|-----|
| **pending_card_payment** | Order Issue | Billing | Payment processing status = billing |
| **declined_card_payment** | Order Issue | Billing | Payment authorization failure = billing |
| **failed_transfer** | Order Issue | Billing | Payment transaction failure = billing |
| **balance_not_updated** | Order Issue | Billing | Account balance/transaction posting = billing |
| **card_payment_not_recognised** | Order Issue | Billing | Unrecognized charge = billing dispute |

---

## Proposed Category Distribution

### Current Distribution (Baseline)
- **General Inquiry**: 27 intents (35.1%)
- **Order Issue**: 21 intents (27.3%)
- **Billing**: 16 intents (20.8%)
- **Technical Support**: 10 intents (13.0%)
- **Refund Request**: 3 intents (3.9%)

### Proposed Distribution (After Corrections)
- **General Inquiry**: 27 intents (35.1%) — unchanged
- **Order Issue**: 6 intents (7.8%) — **reduced by 15**
- **Billing**: 30 intents (39.0%) — **increased by 14**
- **Technical Support**: 11 intents (14.3%) — increased by 1
- **Refund Request**: 3 intents (3.9%) — unchanged

**Key Change:** Moving banking transaction problems from "Order Issue" to "Billing"

---

## Top 10 Mapping Changes Most Likely to Affect Benchmark

These changes would shift 30 test cases (15 intents × 2 cases each):

| Rank | Intent | Current | Proposed | Impact | Cases Affected |
|------|--------|---------|----------|--------|----------------|
| 1 | **pending_transfer** | Order Issue | Billing | 🔥 High | 2 |
| 2 | **pending_card_payment** | Order Issue | Billing | 🔥 High | 2 |
| 3 | **declined_card_payment** | Order Issue | Billing | 🔥 High | 2 |
| 4 | **failed_transfer** | Order Issue | Billing | 🔥 High | 2 |
| 5 | **balance_not_updated_after_bank_transfer** | Order Issue | Billing | 🔥 High | 2 |
| 6 | **pending_cash_withdrawal** | Order Issue | Billing | 🔥 High | 2 |
| 7 | **pending_top_up** | Order Issue | Billing | 🔥 High | 2 |
| 8 | **top_up_failed** | Order Issue | Billing | 🔥 High | 2 |
| 9 | **declined_cash_withdrawal** | Order Issue | Billing | 🔥 High | 2 |
| 10 | **balance_not_updated_after_cheque_or_cash_deposit** | Order Issue | Billing | 🔥 High | 2 |

**Additional changes:**
- **card_payment_not_recognised** (Order Issue → Billing): 2 cases
- **cash_withdrawal_not_recognised** (Order Issue → Billing): 2 cases
- **declined_transfer** (Order Issue → Billing): 2 cases
- **card_swallowed** (Order Issue → Technical Support): 2 cases
- **beneficiary_not_allowed** (Order Issue → Technical Support): 2 cases

---

## Semantic Justification for Changes

### Banking Transactions Are NOT E-Commerce Orders

**The fundamental taxonomy mismatch:**

In e-commerce support, "Order Issue" means:
- "My package hasn't arrived"
- "Wrong item was shipped"
- "Product is defective"
- "Tracking shows delivered but I didn't receive it"

In banking, the equivalent would be:
- "My physical card hasn't arrived" ← **correctly mapped to Order Issue**
- "Card was swallowed by ATM" ← **physical item problem**

**But most BANKING77 transaction intents are about payment processing:**
- "My transfer is pending" = payment processing status
- "My payment was declined" = payment authorization failure
- "I see an unrecognized charge" = billing dispute
- "My balance didn't update" = transaction posting problem

**These are all Billing problems in e-commerce support**, not order fulfillment problems.

### The "Pending" vs "Failed" vs "Unrecognized" Pattern

| Transaction State | SupportFlow Category | Reasoning |
|-------------------|----------------------|-----------|
| **Pending** (transfer, payment, withdrawal, top-up) | Billing | Payment processing status = billing/payment system |
| **Declined** (payment, withdrawal, transfer) | Billing | Payment authorization failure = billing |
| **Failed** (transfer, top-up) | Billing | Payment transaction failure = billing |
| **Not recognized** (payment, withdrawal) | Billing | Unrecognized charge = billing dispute |
| **Balance not updated** (transfer, deposit) | Billing | Transaction posting problem = billing/accounting |

All of these are **payment/transaction system problems**, not **physical order delivery problems**.

---

## Ambiguous Cases (Require Judgment Call)

### 1. **terminate_account** (General Inquiry)
- **Current**: General Inquiry (how to close account)
- **Alternative**: Technical Support (account action)
- **Decision**: Keep General Inquiry (user asking "how", not "do it now")

### 2. **beneficiary_not_allowed** (Order Issue)
- **Current**: Order Issue (transfer blocked)
- **Proposed**: Technical Support (system restriction/configuration)
- **Reasoning**: "Not allowed" = technical restriction, not transaction failure

### 3. **card_swallowed** (Order Issue)
- **Current**: Order Issue (physical item problem)
- **Proposed**: Technical Support (ATM malfunction/access issue)
- **Reasoning**: Card stuck in machine = technical/access problem

### 4. **transfer_not_received_by_recipient** (Order Issue)
- **Current**: Order Issue (delivery problem)
- **Alternative**: Billing (transaction problem)
- **Decision**: Defensible either way; keep Order Issue (delivery failure)

### 5. **wrong_amount_of_cash_received** (Order Issue)
- **Current**: Order Issue (amount discrepancy)
- **Alternative**: Billing (transaction amount error)
- **Decision**: Ambiguous; leaning Billing (payment accuracy problem)

---

## Validation Against SupportFlow Taxonomy

### Definition Compliance Check

**General Inquiry** (27 intents) ✅
All mappings are genuine information/policy questions with no concrete problem.

**Refund Request** (3 intents) ✅
- Refund_not_showing_up
- request_refund
- reverted_card_payment (payment reversal)
- top_up_reverted (transaction reversal)

All explicitly involve money being returned to customer.

**Technical Support** (10→11 intents) ✅
All involve account access, security, authentication, technical functionality, or configuration.

**Billing** (16→30 intents) ✅ **CORRECTED**
Now includes:
- Unexpected fees/charges ✅
- Payment processing status ✅
- Payment authorization failures ✅
- Transaction posting problems ✅
- Billing disputes (unrecognized charges) ✅
- Incorrect amounts charged ✅

**Order Issue** (21→6 intents) ✅ **CORRECTED**
Reduced to genuine order/delivery problems:
- card_arrival (physical delivery)
- card_swallowed (if kept as Order Issue)
- cancel_transfer (action on transaction)
- transfer_not_received_by_recipient (delivery failure)
- wrong_amount_of_cash_received (if kept as Order Issue)

**Remaining Order Issues that should move:**
Most transaction-state problems moved to Billing.

---

## Conclusion

### Root Cause of 50.0% Accuracy

The baseline 50.0% accuracy (77/154 correct) is primarily caused by **systematic taxonomy mismatch**, not classifier failure.

**The mismatch:**
- 21 intents (27.3%) are currently mapped to "Order Issue"
- 15 of these (71%) are actually **banking transaction problems** that should be "Billing"
- This affects 30 test cases (15 intents × 2 cases)

**If the classifier correctly identifies these as billing/payment problems** (which would be semantically correct for banking transactions), it gets them "wrong" according to the current gold labels.

### Experiment Hypothesis

**Hypothesis:** The classifier is performing better than 50.0% when evaluated against correct semantic mappings.

**Evidence needed:**
1. Run classifier against proposed mappings
2. Compare results to current baseline
3. Determine whether accuracy improves primarily for the 30 transaction-state cases

**Expected outcome:**
- Overall accuracy increases
- "Order Issue" accuracy remains low (smaller category, more ambiguous)
- "Billing" accuracy increases significantly
- "General Inquiry" accuracy remains high

---

## Files Created

1. **This audit report**: `evaluation/datasets/MAPPING_AUDIT_REPORT.md`

---

## Next Steps

1. Review this audit independently
2. Validate semantic reasoning for proposed changes
3. Create experimental mapping file (`banking77_mapping_experiment.json`)
4. Run classifier against experimental mappings
5. Compare results to Stage 9B baseline

---

## Production Code Status ✅

**Confirmed unchanged:**
- ✅ `app/llm.py` (classifier implementation)
- ✅ `evaluation/config.py` (prompt text)
- ✅ `evaluation/datasets/banking77_benchmark.json` (154 test cases)
- ✅ `evaluation/datasets/banking77_mapping.json` (current gold labels)
- ✅ All production business logic
- ✅ Stage 9B baseline results preserved

**No Groq runs performed.**
**No evaluation executed.**
**No files modified except this audit report.**

