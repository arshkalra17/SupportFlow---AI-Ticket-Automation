"""
Shared configuration for the SupportFlow evaluation benchmark.

All values here are read-only constants.  No production business logic lives here.
"""

import hashlib
import os
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────
EVAL_ROOT      = Path(__file__).parent
DATASETS_DIR   = EVAL_ROOT / "datasets"
RESULTS_DIR    = EVAL_ROOT / "results"
CACHE_DIR      = RESULTS_DIR / "cache"

RESULTS_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ── Evaluation database ────────────────────────────────────────────────
# Dedicated isolated database — never the development or test database.
EVAL_DATABASE_URL = os.getenv(
    "EVAL_DATABASE_URL",
    "postgresql://supportflow_user:supportflow_password@localhost:5432/supportflow_eval",
)

# ── LLM model (mirrors production) ────────────────────────────────────
EVAL_MODEL = "openai/gpt-oss-120b"

# ── Prompt versions ────────────────────────────────────────────────────
# A short hash of the system prompt text so cache keys are invalidated
# automatically if the prompt changes.  Computed at import time.

_CLASSIFICATION_SYSTEM_PROMPT = (
    "You are an AI customer support ticket classifier. "
    "Analyze the customer message and classify it into exactly these three fields.\n\n"
    "CATEGORY — choose exactly one:\n"
    "  General Inquiry    : Customer is asking how something works, asking about policy, "
    "or providing feedback. No specific action is being requested on an order.\n"
    "  Order Issue        : Customer has a problem with a specific order that needs action "
    "(wrong item, stuck in processing, damaged delivery, duplicate order, etc.).\n"
    "  Refund Request     : Customer is explicitly asking for money back.\n"
    "  Billing            : Payment, charges, invoices, or subscription billing problems.\n"
    "  Technical Support  : App, website, or account access problems.\n\n"
    "PRIORITY — choose exactly one:\n  LOW    : Information request or passive question; no active problem and no urgency.\n  MEDIUM : Active problem but not fully blocking the customer; no financial urgency.\n           A routine refund request for a small amount is MEDIUM, not HIGH.\n  HIGH   : Significant problem that is actively blocking the customer, or involves a\n           meaningful financial concern (e.g. large unexpected charge, billing dispute).\n  URGENT : Customer is completely blocked from using the service (e.g. checkout down,\n           cannot log in to complete a purchase), OR a fraud or duplicate-charge situation\n           is reported. Example: being charged twice for the same order = URGENT.\n\nSENTIMENT — choose exactly one:\n"
    "  POSITIVE   : Customer is expressing satisfaction, gratitude, or praise.\n"
    "  NEUTRAL    : Customer is asking a question or requesting information; "
    "no problem is being reported.\n"
    "  NEGATIVE   : Customer is reporting a problem, expressing disappointment, or "
    "describing something that went wrong — even if the language is calm and factual.\n"
    "  FRUSTRATED : Customer is reporting a problem AND showing anger, exasperation, or "
    "urgency through strong language (\"unacceptable\", \"completely broken\", \"I demand\"), "
    "explicit contradiction (\"even though\", \"despite\"), repeated failure "
    "(\"never arrives\", \"keeps crashing\"), or urgent/forceful phrasing "
    "(\"fix this immediately\", \"what is going on\").\n\n"
    "You MUST respond ONLY with a valid JSON object with no markdown formatting, "
    "preambles, or explanations. "
    "The JSON object MUST contain exactly these three keys: "
    "\"category\", \"priority\", and \"sentiment\"."
)

_TOOL_SELECTION_SYSTEM_PROMPT = (
    "You are a helpful customer support AI assistant for SupportFlow. "
    "Answer customer questions politely and concisely. "
    "If the customer asks about an order status and provides an order ID, "
    "use the 'get_order_status' tool to look it up. "
    "If the customer wants a replacement for a damaged or defective order, "
    "first check the order status with 'get_order_status', then ALWAYS use "
    "'create_replacement_request' to attempt the replacement. "
    "If the customer requests a refund for an order, use the 'issue_refund' tool "
    "with the order_id and requested amount. "
    "You must NOT decide whether an order is eligible or whether approval is required — "
    "the backend will make that determination. Always call the tool and "
    "relay the backend result to the customer. "
    "Do not invent or guess order details. "
    "Do not fabricate tool results."
)


def _short_hash(text: str) -> str:
    """Returns the first 8 hex characters of the SHA-256 of text."""
    return hashlib.sha256(text.encode()).hexdigest()[:8]


CLASSIFICATION_PROMPT_VERSION  = f"v1-{_short_hash(_CLASSIFICATION_SYSTEM_PROMPT)}"
TOOL_SELECTION_PROMPT_VERSION  = f"v1-{_short_hash(_TOOL_SELECTION_SYSTEM_PROMPT)}"

# Export prompt version for observability
_CLASSIFICATION_PROMPT_VERSION = CLASSIFICATION_PROMPT_VERSION

# ── Rate limiting between sequential Groq calls ────────────────────────
# Keeps the benchmark quota-conscious without sacrificing correctness.
GROQ_CALL_DELAY_SECONDS = 1.0

# ── RAG retrieval parameters (must match production) ──────────────────
RAG_TOP_K              = 3
RAG_SIMILARITY_THRESHOLD = 0.15   # from app/rag.py

# ── Evaluation order IDs ───────────────────────────────────────────────
# These IDs are seeded into supportflow_eval only.
# They are in the 5000–5030 range, far from any dev data.
EVAL_ORDER_IDS = {
    # customer_id → [order_ids]
    1: [5001, 5002, 5003, 5004],
    2: [5011, 5012],
    3: [5021, 5022],
}
EVAL_ORDER_STATUS = {
    5001: "SHIPPED",
    5002: "DELIVERED",
    5003: "PROCESSING",
    5004: "CANCELLED",
    5011: "DELIVERED",
    5012: "PROCESSING",
    5021: "SHIPPED",
    5022: "DELIVERED",
}

# ── Evaluation customer IDs ────────────────────────────────────────────
EVAL_CUSTOMER_IDS = [1, 2, 3]
