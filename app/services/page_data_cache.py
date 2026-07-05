"""Short-lived in-memory cache for SSR page payloads (fast repeat loads)."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

_cache: dict[str, tuple[float, Any]] = {}
_inflight: dict[str, asyncio.Task[Any]] = {}
DEFAULT_TTL_SECONDS = 120


async def get_or_build(
    key: str,
    ttl_seconds: int,
    builder: Callable[[], Awaitable[Any]],
) -> Any:
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and (now - hit[0]) < ttl_seconds:
        return hit[1]

    task = _inflight.get(key)
    if task is None or task.done():
        task = asyncio.create_task(_run_build(key, builder))
        _inflight[key] = task
    return await task


async def _run_build(key: str, builder: Callable[[], Awaitable[Any]]) -> Any:
    try:
        data = await builder()
        if data is not None:
            _cache[key] = (time.monotonic(), data)
        return data
    finally:
        _inflight.pop(key, None)


def invalidate_prefix(prefix: str) -> None:
    for key in list(_cache):
        if key.startswith(prefix):
            del _cache[key]
