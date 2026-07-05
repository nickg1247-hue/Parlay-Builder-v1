"""Short-lived in-memory cache for SSR page payloads (fast repeat loads)."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

_lock = asyncio.Lock()
_cache: dict[str, tuple[float, Any]] = {}
DEFAULT_TTL_SECONDS = 60


async def get_or_build(
    key: str,
    ttl_seconds: int,
    builder: Callable[[], Awaitable[Any]],
) -> Any:
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and (now - hit[0]) < ttl_seconds:
        return hit[1]

    async with _lock:
        hit = _cache.get(key)
        if hit and (now - hit[0]) < ttl_seconds:
            return hit[1]
        data = await builder()
        _cache[key] = (now, data)
        return data


def invalidate_prefix(prefix: str) -> None:
    for key in list(_cache):
        if key.startswith(prefix):
            del _cache[key]
