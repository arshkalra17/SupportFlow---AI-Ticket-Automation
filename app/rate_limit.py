"""Redis rate limiting: per-customer request limits and a shared model-token budget.

Two protections, both built on one primitive, a sliding-window counter:

* **Per-customer request limit.** One customer cannot use more than
  RATE_LIMIT_PER_MINUTE LLM-backed requests a minute. Over the limit the API
  answers 429 with an honest Retry-After.
* **Shared token budget.** The free Groq tier caps the main model by tokens per
  minute and per day for the whole organisation. Every response's real token
  usage is added to two shared counters; once either is spent, new requests get
  429 instead of being sent to Groq to fail there (each failure costs retries,
  trips the circuit breaker and delays everyone).

The sliding-window counter keeps two fixed buckets (current and previous) and
weights the previous one by how much of it still overlaps the window. That is
two Redis keys per limit, no Lua, and no 2x burst at bucket boundaries as a
plain fixed window would allow. It is an approximation: it assumes traffic was
evenly spread inside the previous bucket.

If Redis is unreachable the limiter fails open (requests are allowed and a
warning is logged), the same choice as the telemetry layer: a broken limiter
must not take the API down. The cost is that limits are not enforced during a
Redis outage.

Settings (read on every call, so they can change without a restart):

    RATE_LIMIT_ENABLED         1       set 0 to turn everything off
    RATE_LIMIT_PER_MINUTE      10      requests per customer per minute
    TOKEN_BUDGET_PER_MINUTE    7000    shared tokens per minute (Groq free tier: 8000)
    TOKEN_BUDGET_PER_DAY       180000  shared tokens per rolling day (free tier: 200000)
    TOKEN_BUDGET_MODEL         the default Groq model; only its usage is counted

Set a token window to 0 to disable it.
"""

from __future__ import annotations

import logging
import math
import os
import time

# pyrefly: ignore [missing-import]
import redis
from fastapi import Depends, HTTPException, Request, status

from app.auth import get_current_customer
from app.models import Customer

logger = logging.getLogger(__name__)

KEY_PREFIX = "supportflow:ratelimit"
MINUTE = 60
DAY = 24 * 60 * 60
_EPSILON = 1e-9  # float slack at exact window boundaries


class RateLimited(Exception):
    """A limit is spent; `retry_after` is the whole seconds until it would not be."""

    def __init__(self, scope: str, limit: int, retry_after: int):
        super().__init__(f"{scope} limit of {limit} reached; retry in {retry_after}s")
        self.scope = scope
        self.limit = limit
        self.retry_after = retry_after


def enabled() -> bool:
    return os.getenv("RATE_LIMIT_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _redis():
    # Looked up at call time so tests can swap app.queue.redis_client.
    import app.queue as queue_module
    return queue_module.redis_client


# ── sliding-window counter ────────────────────────────────────────────

def _position(now: float, window: int) -> tuple[int, float]:
    """(bucket index, how far into the bucket we are, 0..1)."""
    index = int(now // window)
    return index, (now - index * window) / window


def _key(scope: str, ident: str, index: int) -> str:
    return f"{KEY_PREFIX}:{scope}:{ident}:{index}"


def _seconds_until_allowed(cur_total: int, prev: int, frac: float, limit: int, window: int) -> int:
    """Seconds until `cur_total + prev * (1 - fraction)` is at or under `limit`.

    `cur_total` is the current bucket's count including the unit being asked for.
    While it fits (<= limit) only the previous bucket's weight has to fade. If it
    does not fit, nothing in this bucket helps: wait for the next bucket, in
    which this one becomes the previous and the unit is the only current hit.
    """
    if cur_total <= limit:
        if prev <= 0:
            return 0
        needed = 1 - (limit - cur_total) / prev
        return max(0, math.ceil((needed - frac) * window - _EPSILON))
    stored = cur_total - 1
    needed = max(0.0, 1 - (limit - 1) / stored) if stored > 0 else 0.0
    return math.ceil((1 - frac) * window + needed * window - _EPSILON)


def hit(scope: str, ident: str, limit: int, window: int = MINUTE, now: float | None = None) -> None:
    """Count one request; raise RateLimited if it would exceed `limit` in the window.

    A rejected request is taken back out so that it does not push the retry time
    further away. Redis errors fail open.
    """
    if not enabled() or limit <= 0:
        return
    now = time.time() if now is None else now
    index, frac = _position(now, window)
    current, previous = _key(scope, ident, index), _key(scope, ident, index - 1)
    try:
        r = _redis()
        pipe = r.pipeline()
        pipe.incr(current)
        pipe.expire(current, 2 * window + 1)
        pipe.get(previous)
        count, _, prev_raw = pipe.execute()
        prev = int(prev_raw or 0)
        if count + prev * (1 - frac) > limit + _EPSILON:
            r.decr(current)
            raise RateLimited(scope, limit, max(1, _seconds_until_allowed(count, prev, frac, limit, window)))
    except (redis.RedisError, OSError) as err:
        logger.warning("Rate limiter unavailable, allowing request (%s): %s", scope, err)


def _estimate(scope: str, ident: str, window: int, now: float) -> tuple[float, int, int, float]:
    """(sliding estimate, current bucket, previous bucket, fraction)."""
    index, frac = _position(now, window)
    cur_raw, prev_raw = _redis().mget(_key(scope, ident, index), _key(scope, ident, index - 1))
    cur, prev = int(cur_raw or 0), int(prev_raw or 0)
    return cur + prev * (1 - frac), cur, prev, frac


# ── per-customer requests ─────────────────────────────────────────────

def check_customer_rate(scope: str, ident: str, now: float | None = None) -> None:
    """Spend one request from this identity's per-minute allowance."""
    hit(scope, ident, _int_env("RATE_LIMIT_PER_MINUTE", 10), MINUTE, now)


# ── shared model-token budget ─────────────────────────────────────────

def _budget_windows() -> list[tuple[str, int, int]]:
    return [
        ("tokens_minute", MINUTE, _int_env("TOKEN_BUDGET_PER_MINUTE", 7000)),
        ("tokens_day", DAY, _int_env("TOKEN_BUDGET_PER_DAY", 180000)),
    ]


def _budget_model() -> str:
    configured = os.getenv("TOKEN_BUDGET_MODEL")
    if configured:
        return configured
    from app.llm_client import model_for
    return model_for("groq")


def record_token_usage(model: str, tokens: int, now: float | None = None) -> None:
    """Add a finished call's tokens to the shared counters (only the budgeted model)."""
    if not enabled() or tokens <= 0 or model != _budget_model():
        return
    now = time.time() if now is None else now
    try:
        r = _redis()
        pipe = r.pipeline()
        for name, window, limit in _budget_windows():
            if limit <= 0:
                continue
            key = _key(name, "shared", _position(now, window)[0])
            pipe.incrby(key, tokens)
            pipe.expire(key, 2 * window + 1)
        pipe.execute()
    except (redis.RedisError, OSError) as err:
        logger.warning("Token budget unavailable, usage not recorded: %s", err)


def check_token_budget(now: float | None = None) -> None:
    """Raise RateLimited if the shared minute or day budget is already spent.

    The check happens before the work starts, using usage already recorded, so a
    request that begins just under the budget can overshoot it by one call; the
    next request then waits. That is the price of not reserving tokens up front.
    """
    if not enabled():
        return
    now = time.time() if now is None else now
    try:
        for name, window, limit in _budget_windows():
            if limit <= 0:
                continue
            estimate, cur, prev, frac = _estimate(name, "shared", window, now)
            if estimate >= limit:
                wait = _seconds_until_allowed(cur + 1, prev, frac, limit, window)
                raise RateLimited(name, limit, max(1, wait))
    except (redis.RedisError, OSError) as err:
        logger.warning("Token budget unavailable, allowing request: %s", err)


# ── FastAPI dependencies ──────────────────────────────────────────────

def _too_many(err: RateLimited) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={"error": "rate_limited", "scope": err.scope, "retry_after_seconds": err.retry_after},
        headers={"Retry-After": str(err.retry_after), "X-RateLimit-Limit": str(err.limit)},
    )


def enforce_support_limits(current_customer: Customer = Depends(get_current_customer)) -> None:
    """Per-customer request limit, then the shared model-token budget."""
    try:
        check_customer_rate("support", f"customer:{current_customer.id}")
        check_token_budget()
    except RateLimited as err:
        raise _too_many(err)


def enforce_ticket_limit(request: Request) -> None:
    """Per-customer limit for ticket creation; anonymous callers are limited by IP.

    The ticket endpoint accepts an optional token, so identity is read the same
    lenient way it is there. Behind a proxy `request.client` is the proxy, so
    configure the proxy to pass the real address before relying on the IP limit.
    """
    ident = None
    authorization = request.headers.get("authorization", "")
    if authorization.startswith("Bearer "):
        from app.auth import decode_access_token
        try:
            sub = decode_access_token(authorization.removeprefix("Bearer ").strip()).get("sub")
            ident = f"customer:{int(sub)}" if sub else None
        except Exception:
            ident = None
    if ident is None:
        ident = f"ip:{request.client.host if request.client else 'unknown'}"
    try:
        check_customer_rate("ticket", ident)
    except RateLimited as err:
        raise _too_many(err)
