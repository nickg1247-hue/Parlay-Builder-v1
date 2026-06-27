"""Shared helpers for the prop engine (no heavy imports)."""

from __future__ import annotations


def recent_game_window(values: list[float] | tuple[float, ...], n: int) -> list[float]:
    """Most recent n games from a chronological (oldest-first) log."""
    if not values or n <= 0:
        return []
    return list(values[-n:])
