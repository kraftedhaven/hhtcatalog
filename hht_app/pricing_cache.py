"""Small TTL cache for pricing lookups; safe fallback when no shared cache is configured."""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
TTL_SECONDS = int(os.environ.get("PRICING_CACHE_TTL_SECONDS", "900"))


def key(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def get(cache_key: str) -> dict[str, Any] | None:
    value = _CACHE.get(cache_key)
    if not value:
        return None
    expires, result = value
    if expires <= time.monotonic():
        _CACHE.pop(cache_key, None)
        return None
    return dict(result)


def put(cache_key: str, result: dict[str, Any]) -> None:
    _CACHE[cache_key] = (time.monotonic() + TTL_SECONDS, dict(result))


def clear() -> None:
    _CACHE.clear()
