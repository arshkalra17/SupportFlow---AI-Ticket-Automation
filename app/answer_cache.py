"""Redis cache for generated policy answers (the RAG path).

A policy answer depends only on the question and the policy text retrieved for
it, never on who is asking, so one customer's answer can serve another's. The
cache key hashes the normalised question *and the retrieved context and prompt
version*, which means:

* a changed knowledge-base article or a changed prompt is a different key, so
  stale policy is never served after an edit; no invalidation step exists to
  forget;
* only exact repeats (after lower-casing and collapsing whitespace) hit.
  Semantic matching was left out on purpose: a near-duplicate question can have
  a different right answer ("can I return it" vs "can I return it after 30
  days"), and a wrong cached policy answer costs more than a model call.

A hit skips the model call: about 450 `gpt-oss-120b` tokens and one round trip
(measured on a saved policy case: 375 prompt + 77 completion tokens).
Retrieval still runs on every request, so the abstain-on-no-match behaviour is
unchanged and out-of-scope questions are never cached.

Off by default so that evaluation runs measure the model, not the cache; set
ANSWER_CACHE_ENABLED=1 to turn it on. ANSWER_CACHE_TTL_SECONDS (default 3600)
bounds how long an answer lives. Redis errors fail open (a miss).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re

# pyrefly: ignore [missing-import]
import redis

logger = logging.getLogger(__name__)

KEY_PREFIX = "supportflow:answercache"


def enabled() -> bool:
    return os.getenv("ANSWER_CACHE_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")


def _ttl() -> int:
    try:
        return max(1, int(os.getenv("ANSWER_CACHE_TTL_SECONDS", "3600")))
    except ValueError:
        return 3600


def _redis():
    import app.queue as queue_module  # looked up at call time so tests can swap it
    return queue_module.redis_client


def make_key(query: str, context_block: str, prompt_version: str) -> str:
    normalised = re.sub(r"\s+", " ", query.strip().lower())
    digest = hashlib.sha256(
        "\x1f".join((prompt_version, normalised, context_block)).encode("utf-8")
    ).hexdigest()
    return f"{KEY_PREFIX}:{digest}"


def get(key: str) -> dict | None:
    if not enabled():
        return None
    try:
        raw = _redis().get(key)
        return json.loads(raw) if raw else None
    except (redis.RedisError, OSError, ValueError) as err:
        logger.warning("Answer cache unavailable, treating as a miss: %s", err)
        return None


def put(key: str, answer: str, model: str) -> None:
    if not enabled() or not answer:
        return
    try:
        _redis().set(key, json.dumps({"answer": answer, "model": model}), ex=_ttl())
    except (redis.RedisError, OSError) as err:
        logger.warning("Answer cache unavailable, answer not stored: %s", err)
