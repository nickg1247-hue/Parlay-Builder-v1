"""UFC home dashboard chip — next card summary without rebuilding full board."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.services.schedule_ufc import get_ufc_schedule
from app.services.ufc_slate_predictions import predict_slate


def get_ufc_home_chip() -> dict[str, Any]:
    """Lightweight next-UFC-card summary for /api/home/today."""
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
        return empty

    fights = list(schedule.get("games") or [])
    if not fights:
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
    try:
        preds = predict_slate(slate_day)
        plus_ev = sum(1 for p in preds.values() if p.get("plus_ev_ml"))
        if not headline and preds:
            top = max(
                preds.values(),
                key=lambda p: float(p.get("model_prob_home") or 0.5),
            )
            headline = f"{top.get('home_team')} vs {top.get('away_team')}"
    except FileNotFoundError:
        pass

    return {
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
