"""Disk-only data for SSR page loads — never call external APIs during HTML render."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.services.schedule_mlb import load_mlb_schedule_cache
from app.services.slate_clock import slate_today


def local_mlb_scores(game_date: date | None = None) -> dict[str, Any]:
    """MLB scores/ticker payload from on-disk schedule cache (no HTTP)."""
    game_date = game_date or slate_today()
    sched = load_mlb_schedule_cache(game_date)
    games = []
    for row in sched.get("games") or []:
        g = dict(row)
        g.setdefault("sport", "mlb")
        games.append(g)
    return {
        "sport": "mlb",
        "date": sched.get("date") or game_date.isoformat(),
        "requested_date": game_date.isoformat(),
        "resolved_date": sched.get("date") or game_date.isoformat(),
        "days_ahead": 0,
        "auto_advanced": False,
        "games": games,
        "games_count": len(games),
        "cached_at": sched.get("cached_at"),
        "cache_hit": True,
        "source": sched.get("source", "schedule_cache"),
    }


def local_nfl_scores(game_date: date | None = None) -> dict[str, Any]:
    """NFL scores/ticker from on-disk schedule cache (no ESPN on HTML render)."""
    from app.services.schedule_nfl import schedule_cache_path

    game_date = game_date or slate_today()
    path = schedule_cache_path(game_date)
    games: list[dict[str, Any]] = []
    cached_at = None
    source = "schedule_cache"
    resolved = game_date.isoformat()
    if path.exists():
        try:
            import json

            payload = json.loads(path.read_text(encoding="utf-8"))
            for row in payload.get("games") or []:
                g = dict(row)
                g.setdefault("sport", "nfl")
                games.append(g)
            cached_at = payload.get("cached_at")
            source = payload.get("source", "schedule_cache")
            resolved = str(payload.get("date") or resolved)
        except (json.JSONDecodeError, OSError):
            games = []
    return {
        "sport": "nfl",
        "date": resolved,
        "requested_date": game_date.isoformat(),
        "resolved_date": resolved,
        "days_ahead": 0,
        "auto_advanced": False,
        "games": games,
        "games_count": len(games),
        "cached_at": cached_at,
        "cache_hit": path.exists(),
        "source": source,
    }


EMPTY_UFC_CHIP: dict[str, Any] = {
    "available": False,
    "card_date": None,
    "days_ahead": 0,
    "fight_count": 0,
    "event_name": None,
    "headline_fight": None,
    "plus_ev_count": 0,
    "href": "/ufc",
    "message": None,
}
