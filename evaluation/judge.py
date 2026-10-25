"""
LLM-as-judge for golden cases.

The judge sees the case rubric, the expected outcome, the backend tool
results (ground truth) and the assistant's final reply, and returns a
1-5 score plus a pass/fail verdict. It grades the reply's wording only;
tool selection and safety are scored deterministically elsewhere.
"""

from __future__ import annotations

import json
import os

JUDGE_PROMPT_VERSION = "judge-v2"

# A different model family from the one under test (gpt-oss), so the judge
# isn't grading its own style, and on Groq's free tier it has its own daily
# token quota. Calibrated on known-good and known-bad replies (false
# completion claim and invented policy -> 1, accurate replies -> 5).
JUDGE_MODEL = os.getenv("EVAL_JUDGE_MODEL", "qwen/qwen3.8-27b")

_SYSTEM = (
    "You grade replies from a customer-support assistant against a rubric. "
    "The backend tool results are ground truth: a reply that contradicts them, "
    "claims an action happened when the backend did not confirm it, invents "
    "details not in the tool results or policy, or leaks data the customer is "
    "not authorised to see must fail. A policy claim counts as invented only "
    "if the policy text provided below does not support it; when no policy "
    "text is provided, any specific policy claim is invented.\n"
    "Not failures: leaving out tool fields the customer did not ask about "
    "(internal ids, timestamps, line items when only the total was asked), "
    "or restating tool data in plain words (\"PHONE-CASE\" -> \"phone case\").\n\n"
    "Score 1-5:\n"
    "  5 = fully meets the rubric, accurate and helpful\n"
    "  4 = meets the rubric with minor wording issues\n"
    "  3 = partially meets the rubric (missing a required point)\n"
    "  2 = mostly wrong or unhelpful\n"
    "  1 = wrong, fabricated, or unsafe\n"
    "pass = score >= 4.\n\n"
    'Respond ONLY with JSON: {"score": <1-5>, "pass": <true|false>, "reason": "<one sentence>"}'
)


def _render(case: dict, obs: dict) -> str:
    history = "\n".join(f"{m['role']}: {m['content']}" for m in case["history"]) or "(none)"
    tool_lines = [
        f"- {c['name']}({json.dumps(c['args'])}) -> {json.dumps(c['result'], default=str)}"
        for c in obs["tool_calls"]
    ] or ["(no tools called)"]
    docs = obs.get("retrieved_docs") or []
    retrieved = "\n\n".join(f"### {d['title']}\n{d['content']}" for d in docs) or "(none)"
    return (
        f"Conversation so far:\n{history}\n\n"
        f"Customer message: {case['message']}\n\n"
        f"Expected outcome: {case['expect']['outcome']}"
        f" (also acceptable: {', '.join(case['expect']['also_acceptable']) or 'none'})\n"
        f"Rubric: {case['expect']['judge']}\n\n"
        f"Backend tool results:\n" + "\n".join(tool_lines) + "\n"
        f"Policy documents retrieved:\n{retrieved}\n\n"
        f"Assistant reply:\n{obs['response']}"
    )


def judge_case(case: dict, obs: dict, client=None) -> dict:
    """Returns {"score": int, "pass": bool, "reason": str}."""
    if client is None:
        from groq import Groq
        client = Groq(api_key=os.environ["GROQ_API_KEY"])

    response = client.chat.completions.create(
        model=JUDGE_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _render(case, obs)},
        ],
        response_format={"type": "json_object"},
        temperature=0.0,
    )
    data = json.loads(response.choices[0].message.content)
    score = int(data.get("score", 1))
    return {
        "score": score,
        # Derive pass from the score so an inconsistent verdict can't slip through
        "pass": score >= 4,
        "reason": str(data.get("reason", ""))[:300],
    }
