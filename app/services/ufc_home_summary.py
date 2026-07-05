"""UFC home dashboard chip — next card summary without rebuilding full board."""

from __future__ import annotations

import time
from datetime import date
from typing import Any

from app.services.schedule_ufc import get_ufc_schedule

_CHIP_CACHE: dict[str, Any] | None = None
_CHIP_CACHE_AT: float = 0.0
_CHIP_CACHE_TTL_SECONDS = 600


def get_ufc_home_chip() -> dict[str, Any]:
    """Lightweight next-UFC-card summary for /api/home/today."""
    global _CHIP_CACHE, _CHIP_CACHE_AT
    now = time.monotonic()
    if _CHIP_CACHE is not None and (now - _CHIP_CACHE_AT) < _CHIP_CACHE_TTL_SECONDS:
        return _CHIP_CACHE

    empty: dict[str, Any] = {
        "available": False,
        "card_date": None,
        "days_ahead": 0,
        "fight_count": 0,
        "event_name": None,
        "headline_fight": None,
        "plus_ev_count": 0,
        "href": "/ufc",
        "message": "No upcoming UFC card in the next 30 days.",
    }
    try:
        schedule = get_ufc_schedule(None, auto_resolve=True)
    except Exception:
        _CHIP_CACHE = empty
        _CHIP_CACHE_AT = now
        return empty

    fights = list(schedule.get("games") or [])
    if not fights:
        _CHIP_CACHE = empty
        _CHIP_CACHE_AT = now
        return empty

    card_date = schedule.get("resolved_date") or schedule.get("date")
    slate_day = date.fromisoformat(str(card_date)[:10])
    event_name = None
    for fight in fights:
        if fight.get("event_name"):
            event_name = fight["event_name"]
            break

    headline = None
    main_fight = next(
        (f for f in fights if (f.get("card_segment") or "").lower() == "main"),
        fights[-1] if fights else None,
    )
    if main_fight:
        home = main_fight.get("home_team") or main_fight.get("home_fighter")
        away = main_fight.get("away_team") or main_fight.get("away_fighter")
        if home and away:
            headline = f"{home} vs {away}"

    plus_ev = 0
    # Headline from schedule only — skip predict_slate (~6s full-card ML) on page load.

    result = {
        "available": True,
        "card_date": slate_day.isoformat(),
        "days_ahead": int(schedule.get("days_ahead") or 0),
        "fight_count": len(fights),
        "event_name": event_name,
        "headline_fight": headline,
        "plus_ev_count": plus_ev,
        "href": f"/ufc?date={slate_day.isoformat()}",
        "message": None,
    }
    _CHIP_CACHE = result
    _CHIP_CACHE_AT = now
    return result
