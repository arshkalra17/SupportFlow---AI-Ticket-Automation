"""
Deterministic checks run before a state-changing tool executes.

The model decides which tool to call, but two mistakes showed up in the
golden eval that the backend could not catch on its own:

  - acting on a question: "Is order 7003 eligible for a replacement?" created
    a replacement, and "is that small enough for a refund to go through?"
    issued one. Questions about eligibility, cost or what would happen are
    not requests; the customer gets the answer and an offer to act.
  - a cleaned-up amount: "Refund -$20" reached issue_refund as amount=20,
    so the backend's amount > 0 rule never saw the negative.

Both checks read only the customer's latest message, so a follow-up such as
"yes, go ahead" after the assistant offers to act is a request.
"""

from __future__ import annotations

import re

STATE_CHANGING_TOOLS = {"cancel_order", "issue_refund", "create_replacement_request"}

# Marker on a tool result the guard produced, so callers and the eval can tell
# a blocked attempt from a backend outcome.
GUARD_KEY = "guard"
NOT_REQUESTED = "action_not_requested"

# Phrasings that ask about an action rather than ask for it.
_QUESTION = re.compile(
    r"\b(?:eligible|eligibility|qualify|qualifies)\b"
    r"|\b(?:small|big|low|high)\s+enough\b"
    r"|\bgo\s+through\b"
    r"|\b(?:can|could|may)\s+i\s+(?:still\s+)?(?:cancel|return)\b"
    r"|\bis\s+it\s+(?:too\s+late|possible)\b"
    r"|\bhow\s+(?:much|long|many)\b"
    r"|\bwhat\s+(?:happens|would\s+happen)\b"
    r"|\bwould\s+(?:i|it|that|this)\b",
    re.IGNORECASE,
)

# Explicit requests win over question phrasing: "Can I get a refund on 7004?
# Please go ahead." or "If it's eligible, replace it."
_REQUEST = re.compile(
    r"\b(?:please|pls|go\s+ahead|proceed|do\s+it|yes|yeah|sure|confirm(?:ed)?)\b"
    r"|\bi(?:'d|\s+would)?\s+(?:want|like|need)\b"
    r"|\b(?:want|need)\s+(?:a|my|the)\s+(?:refund|replacement|money)\b"
    r"|\bif\s+(?:so|yes|it\s+is|it's|eligible|possible|you\s+can)\b"
    r"|^\s*(?:refund|cancel|replace|issue|process|send)\b",
    re.IGNORECASE,
)

# A negative amount the customer typed: "-$20", "$-20", "-20 dollars", "minus $20".
_NEGATIVE_AMOUNT = re.compile(
    r"(?:-\s*\$\s*\d|\$\s*-\s*\d|(?<![\w$])-\d+(?:\.\d+)?\s*(?:dollars|usd)\b|\bminus\s+\$?\s*\d)",
    re.IGNORECASE,
)


def asks_without_requesting(message: str) -> bool:
    """True when the message asks about an action without asking for it."""
    return bool(_QUESTION.search(message or "")) and not _REQUEST.search(message or "")


def states_negative_amount(message: str) -> bool:
    return bool(_NEGATIVE_AMOUNT.search(message or ""))


def check_action(tool_name: str, customer_message: str) -> dict | None:
    """Returns a tool result to use instead of executing, or None to proceed."""
    if tool_name not in STATE_CHANGING_TOOLS:
        return None
    if tool_name == "issue_refund" and states_negative_amount(customer_message):
        # An error result, so it reads like the backend's own amount rule
        return {"error": "Refund amount must be greater than zero; the customer asked for a negative amount."}
    if asks_without_requesting(customer_message):
        return {
            "status": "NOT_EXECUTED",
            GUARD_KEY: NOT_REQUESTED,
            "message": (
                f"{tool_name} was not run: the customer asked a question, not for this action. "
                "Answer it with read-only tools and offer to do it if they want."
            ),
        }
    return None


def blocked_by_guard(result) -> bool:
    return isinstance(result, dict) and result.get(GUARD_KEY) == NOT_REQUESTED
