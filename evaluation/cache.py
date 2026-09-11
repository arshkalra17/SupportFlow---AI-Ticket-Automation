"""
Persistent JSON cache for live LLM benchmark results.

Cache key format:  {case_id}__{model}__{prompt_version}

The cache file is loaded once at EvalCache construction time.
After each LLM call the result is written immediately so a mid-run
crash loses at most the current in-flight call.

Usage:
    cache = EvalCache("classification_cache.json")
    key   = cache.key("cls-001", MODEL, PROMPT_VERSION)

    if cache.has(key):
        result = cache.get(key)
    else:
        result = call_groq(...)
        cache.put(key, result)   # writes to disk immediately
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from evaluation.config import CACHE_DIR


class EvalCache:
    """Thread-safe (single-process) persistent cache backed by a JSON file."""

    def __init__(self, filename: str) -> None:
        self._path: Path = CACHE_DIR / filename
        self._data: dict = self._load()

    # ── Persistence ───────────────────────────────────────────────────

    def _load(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save(self) -> None:
        self._path.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ── Public API ────────────────────────────────────────────────────

    @staticmethod
    def key(case_id: str, model: str, prompt_version: str) -> str:
        """Builds a deterministic cache key."""
        return f"{case_id}__{model}__{prompt_version}"

    def has(self, key: str) -> bool:
        return key in self._data

    def get(self, key: str) -> dict | None:
        return self._data.get(key)

    def put(self, key: str, raw_response: dict, parsed: dict) -> None:
        """Stores a result and immediately persists the cache file."""
        self._data[key] = {
            "key":          key,
            "raw_response": raw_response,
            "parsed":       parsed,
            "timestamp":    datetime.now(timezone.utc).isoformat(),
        }
        self._save()

    def __len__(self) -> int:
        return len(self._data)

    def clear(self) -> None:
        """Clears in-memory and on-disk cache (for --no-cache flag)."""
        self._data = {}
        self._save()
