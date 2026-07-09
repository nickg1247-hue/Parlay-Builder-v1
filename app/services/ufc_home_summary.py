"""UFC home dashboard chip — next card summary without rebuilding full board."""

from __future__ import annotations

import time
from datetime import date
from typing import Any

from app.models.constants import DEFAULT_MIN_EDGE
from app.services.schedule_ufc import get_ufc_schedule
from app.services.ufc_slate_predictions import predict_slate

_CHIP_CACHE: dict[str, Any] | None = None
_CHIP_CACHE_AT: float = 0.0
_CHIP_CACHE_TTL_SECONDS = 600


def _best_ev_pick(
    preds: dict[str, dict[str, Any]],
    *,
    card_date: str,
) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_edge = -1.0
    for fid, row in preds.items():
        for side in ("home", "away"):
            edge = row.get(f"ev_{side}")
            if edge is None:
                continue
            edge_f = float(edge)
            if edge_f < DEFAULT_MIN_EDGE or edge_f <= best_edge:
                continue
            best_edge = edge_f
            fighter = row["home_team"] if side == "home" else row["away_team"]
            best = {
                "fight_id": fid,
                "fighter": fighter,
                "side": side,
                "edge": round(edge_f, 4),
                "american_odds": row.get(f"{side}_ml"),
                "matchup": f"{row.get('away_team')} vs {row.get('home_team')}",
                "href": f"/ufc/game/{fid}?date={card_date}",
            }
    return best


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
        "main_event": None,
        "best_ev_pick": None,
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

    main_fight = next(
        (f for f in fights if (f.get("card_segment") or "").lower() == "main"),
        fights[-1] if fights else None,
    )
    headline = None
    main_event: dict[str, Any] | None = None
    if main_fight:
        home = main_fight.get("home_team") or main_fight.get("home_fighter")
        away = main_fight.get("away_team") or main_fight.get("away_fighter")
        fid = str(main_fight.get("fight_id") or main_fight.get("game_id") or "")
        if home and away:
            headline = f"{home} vs {away}"
            main_event = {
                "fight_id": fid,
                "home": home,
                "away": away,
                "matchup": headline,
                "href": f"/ufc/game/{fid}?date={slate_day.isoformat()}" if fid else None,
            }

    plus_ev = 0
    best_ev: dict[str, Any] | None = None
    try:
        preds = predict_slate(slate_day)
        plus_ev = sum(1 for row in preds.values() if row.get("plus_ev_ml"))
        best_ev = _best_ev_pick(preds, card_date=slate_day.isoformat())
    except (FileNotFoundError, OSError, ValueError):
        best_ev = None

    result = {
        "available": True,
        "card_date": slate_day.isoformat(),
        "days_ahead": int(schedule.get("days_ahead") or 0),
        "fight_count": len(fights),
        "event_name": event_name,
        "headline_fight": headline,
        "main_event": main_event,
        "best_ev_pick": best_ev,
        "plus_ev_count": plus_ev,
        "href": f"/ufc?date={slate_day.isoformat()}",
        "message": None,
    }
    _CHIP_CACHE = result
    _CHIP_CACHE_AT = now
    return result
