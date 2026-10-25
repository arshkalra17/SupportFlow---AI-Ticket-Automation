"""
LLM provider chain: Groq first, Gemini as fallback.

Every LLM call in the app goes through chat_completion(). It tries the
providers in order and moves to the next one only on infrastructure
failures (rate limits, 5xx, timeouts, connection errors). A bad request
or a malformed reply is never retried elsewhere: another model would
not fix it, and it would hide the bug.

A provider that fails is skipped for a cooldown (a simple circuit
breaker), so a provider that is out of quota isn't called once per
request.

Configuration:
    LLM_PROVIDERS   comma-separated order, default "groq,gemini". Providers
                    without an API key are skipped. Set a single provider
                    to pin it (the golden eval does this: a run that
                    switches models halfway measures nothing).
    GROQ_API_KEY,   GROQ_MODEL    (default openai/gpt-oss-120b)
                    GROQ_SMALL_MODEL (default openai/gpt-oss-20b)
    GEMINI_API_KEY, GEMINI_MODEL  (default gemini-3.5-flash)
                    GEMINI_SMALL_MODEL (default: same as GEMINI_MODEL)

Tiers: tier="small" is for simple structured calls (the classifier). On
Groq it runs on gpt-oss-20b with low reasoning effort, which uses a
separate daily token quota from the 120b model and far fewer tokens.

Gemini is called through its OpenAI-compatible endpoint, so messages,
tool schemas and tool calls use the same format as Groq.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

from groq import Groq

logger = logging.getLogger(__name__)

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

DEFAULT_MODELS = {
    ("groq", "default"): "openai/gpt-oss-120b",
    ("groq", "small"): "openai/gpt-oss-20b",
    ("gemini", "default"): "gemini-3.5-flash",
    ("gemini", "small"): "gemini-3.5-flash",
}
_KEY_ENV = {"groq": "GROQ_API_KEY", "gemini": "GEMINI_API_KEY"}
_MODEL_ENV = {
    ("groq", "default"): "GROQ_MODEL",
    ("groq", "small"): "GROQ_SMALL_MODEL",
    ("gemini", "default"): "GEMINI_MODEL",
    ("gemini", "small"): "GEMINI_SMALL_MODEL",
}
# Provider-specific request options per tier. gpt-oss reasons before it
# answers and those tokens count against the quota; a classifier label
# doesn't need much of it (same labels, about half the output tokens).
_EXTRA_KWARGS = {
    ("groq", "small"): {"reasoning_effort": "low"},
}

# Per-call limits. The SDK defaults (10-minute timeout, silent retries)
# would make a slow provider hold a request for minutes instead of
# falling back. SDK retries are off: on a 429 they spend another request
# from a small free-tier quota, and the chain already handles failures.
REQUEST_TIMEOUT_SECONDS = 45
SDK_MAX_RETRIES = 0

# A per-minute limit that asks for a short wait is waited out on the same
# provider when there is no other provider to try: re-running the whole
# request elsewhere (or the whole conversation turn) costs more tokens.
MAX_RATE_LIMIT_WAIT_SECONDS = 20
_RETRY_AFTER = re.compile(r"(?:try again|retry) in (?:(\d+)m(?!s))?([\d.]+)(ms|s)")

# Cooldowns for the circuit breaker
COOLDOWN_SECONDS = 30
DAILY_QUOTA_COOLDOWN_SECONDS = 30 * 60

_FALLBACK_STATUS_CODES = {408, 429, 500, 502, 503, 504}
_unavailable_until: dict[str, float] = {}
_lock = threading.Lock()


class LLMUnavailableError(RuntimeError):
    """Every configured provider failed or is cooling down."""


@dataclass
class LLMResult:
    response: Any  # OpenAI-format chat completion
    provider: str
    model: str


# Token usage per model since the last reset_usage(); the golden eval
# reads it to report tokens per case.
_usage: dict[str, dict[str, int]] = {}


def _record_usage(model: str, response: Any) -> None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    with _lock:
        totals = _usage.setdefault(model, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0})
        totals["calls"] += 1
        totals["prompt_tokens"] += getattr(usage, "prompt_tokens", 0) or 0
        totals["completion_tokens"] += getattr(usage, "completion_tokens", 0) or 0


def get_usage() -> dict[str, dict[str, int]]:
    with _lock:
        return {model: dict(t) for model, t in _usage.items()}


def reset_usage() -> None:
    with _lock:
        _usage.clear()


def configured_providers() -> list[str]:
    """Providers in fallback order that have an API key."""
    order = os.getenv("LLM_PROVIDERS", "groq,gemini")
    names = [p.strip().lower() for p in order.split(",") if p.strip()]
    unknown = [p for p in names if p not in _KEY_ENV]
    if unknown:
        raise ValueError(f"Unknown LLM provider(s) in LLM_PROVIDERS: {unknown}")
    return [p for p in names if os.getenv(_KEY_ENV[p])]


def model_for(provider: str, tier: str = "default") -> str:
    return os.getenv(_MODEL_ENV[(provider, tier)]) or DEFAULT_MODELS[(provider, tier)]


def _client(provider: str):
    key = os.environ[_KEY_ENV[provider]]
    if provider == "groq":
        return Groq(api_key=key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=SDK_MAX_RETRIES)
    from openai import OpenAI
    return OpenAI(api_key=key, base_url=GEMINI_BASE_URL,
                  timeout=REQUEST_TIMEOUT_SECONDS, max_retries=SDK_MAX_RETRIES)


def is_daily_quota(text: str) -> bool:
    """Per-day limits don't clear in seconds. Groq says "tokens per day
    (TPD)"; Gemini names quota metrics like "...RequestsPerDay...". """
    text = text.lower()
    return "per day" in text or "perday" in text or "(tpd)" in text or "(rpd)" in text


def should_fall_back(err: Exception) -> bool:
    """True for failures another provider could get around."""
    status = getattr(err, "status_code", None)
    if status in _FALLBACK_STATUS_CODES:
        return True
    name = type(err).__name__
    return name in {"APIConnectionError", "APITimeoutError", "RateLimitError",
                    "InternalServerError", "ConnectionError", "TimeoutError"}


def retry_after_seconds(err: Exception) -> float | None:
    """The wait a rate-limit error asks for ("try again in 2.55s",
    "retry in 1m40s", "try again in 840ms"), or None."""
    if getattr(err, "status_code", None) != 429 or is_daily_quota(str(err)):
        return None
    match = _RETRY_AFTER.search(str(err))
    if not match:
        return None
    seconds = float(match.group(2)) / (1000 if match.group(3) == "ms" else 1)
    return int(match.group(1) or 0) * 60 + seconds


def _trip(provider: str, err: Exception) -> None:
    cooldown = DAILY_QUOTA_COOLDOWN_SECONDS if is_daily_quota(str(err)) else COOLDOWN_SECONDS
    with _lock:
        _unavailable_until[provider] = time.monotonic() + cooldown
    logger.warning("LLM provider %s unavailable for %ss: %s", provider, cooldown, err)


def _cooling_down(provider: str) -> bool:
    with _lock:
        return _unavailable_until.get(provider, 0) > time.monotonic()


def reset_circuit_breakers() -> None:
    with _lock:
        _unavailable_until.clear()


def chat_completion(tier: str = "default", **kwargs) -> LLMResult:
    """Runs one chat completion on the first provider that answers.

    kwargs are passed through to chat.completions.create (messages, tools,
    tool_choice, response_format, temperature); the model is chosen here
    from the provider and tier ("default" or "small").
    Raises ValueError if no provider has a key, LLMUnavailableError if all
    of them failed, and re-raises a non-fallback error from any provider.
    """
    if tier not in ("default", "small"):
        raise ValueError(f"Unknown LLM tier: {tier!r}")
    providers = configured_providers()
    if not providers:
        raise ValueError(
            "No LLM provider configured: set GROQ_API_KEY or GEMINI_API_KEY"
            " (and check LLM_PROVIDERS)"
        )

    # If every provider is cooling down, try them anyway rather than fail
    # without a call: the cooldown is a guess, the provider may be back.
    ready = [p for p in providers if not _cooling_down(p)] or providers

    errors = []
    for i, provider in enumerate(ready):
        model = model_for(provider, tier)
        extra = _EXTRA_KWARGS.get((provider, tier), {})
        is_last = i == len(ready) - 1
        try:
            try:
                response = _client(provider).chat.completions.create(model=model, **extra, **kwargs)
            except Exception as err:
                wait = retry_after_seconds(err)
                if not (is_last and wait is not None and wait <= MAX_RATE_LIMIT_WAIT_SECONDS):
                    raise
                logger.info("LLM provider %s rate-limited; waiting %.1fs", provider, wait)
                time.sleep(wait + 0.5)
                response = _client(provider).chat.completions.create(model=model, **extra, **kwargs)
        except Exception as err:
            if not should_fall_back(err):
                raise
            _trip(provider, err)
            errors.append(f"{provider}: {err}")
            continue
        _record_usage(model, response)
        if errors:
            logger.info("LLM call served by fallback provider %s after: %s", provider, errors)
        return LLMResult(response=response, provider=provider, model=model)

    raise LLMUnavailableError("All LLM providers failed. " + " | ".join(errors))
