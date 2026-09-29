"""NHL schedule cache — ESPN scoreboard for the requested day, with a short look-ahead."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT
from app.services.scores_nhl import fetch_nhl_scores_day, live_game_record
from app.services.slate_clock import slate_today

logger = logging.getLogger(__name__)

PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
SLATE_LOOKAHEAD_DAYS = 7


def schedule_cache_path(game_date: date) -> Path:
    return PROCESSED_DIR / f"nhl_schedule_{game_date.isoformat()}.json"


def _slate_meta(
    *,
    requested_date: date,
    resolved_date: date,
    days_ahead: int,
    auto_advanced: bool,
) -> dict[str, Any]:
    return {
        "requested_date": requested_date.isoformat(),
        "resolved_date": resolved_date.isoformat(),
        "days_ahead": days_ahead,
        "auto_advanced": auto_advanced,
    }


def _write_schedule_cache(
    game_date: date,
    games: list[dict[str, Any]],
    *,
    source: str,
) -> dict[str, Any]:
    payload = {
        "date": game_date.isoformat(),
        "sport": "nhl",
        "games": games,
        "games_count": len(games),
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
    }
    path = schedule_cache_path(game_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _load_schedule_payload(game_date: date, *, force_live: bool = False) -> dict[str, Any]:
    path = schedule_cache_path(game_date)
    if path.exists() and not force_live:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["source"] = payload.get("source") or "cache"
            return payload
        except (OSError, json.JSONDecodeError):
            logger.warning("NHL schedule cache unreadable: %s", path.name)

    events = fetch_nhl_scores_day(game_date)
    games = [live_game_record(event) for event in events]
    source = "api" if events else "none"
    return _write_schedule_cache(game_date, games, source=source)


def resolve_nhl_slate_date(start: date | None = None) -> tuple[date, int]:
    anchor = start or slate_today()
    for offset in range(SLATE_LOOKAHEAD_DAYS + 1):
        candidate = anchor + timedelta(days=offset)
        payload = _load_schedule_payload(candidate, force_live=False)
        if payload.get("games"):
            return candidate, offset
    return anchor, 0


def get_nhl_schedule(
    game_date: date | None = None,
    *,
    auto_resolve: bool = False,
    force_live: bool = False,
) -> dict[str, Any]:
    requested_date = game_date or slate_today()
    if auto_resolve and game_date is None:
        resolved_date, days_ahead = resolve_nhl_slate_date(None)
        auto_advanced = days_ahead > 0
    else:
        resolved_date = requested_date
        days_ahead = 0
        auto_advanced = False

    payload = _load_schedule_payload(resolved_date, force_live=force_live)
    payload["date"] = resolved_date.isoformat()
    payload.update(
        _slate_meta(
            requested_date=requested_date,
            resolved_date=resolved_date,
            days_ahead=days_ahead,
            auto_advanced=auto_advanced,
        )
    )
    return payload


def _game_from_payload(payload: dict[str, Any], game_id: str) -> dict[str, Any] | None:
    return next(
        (g for g in payload.get("games") or [] if str(g.get("game_id")) == str(game_id)),
        None,
    )


def get_nhl_game(game_id: str, game_date: date | None = None) -> dict[str, Any] | None:
    search_dates: list[date] = []
    if game_date is not None:
        search_dates.append(game_date)
    today = slate_today()
    for offset in range(0, SLATE_LOOKAHEAD_DAYS + 1):
        candidate = today + timedelta(days=offset)
        if candidate not in search_dates:
            search_dates.append(candidate)
    if game_date is not None:
        for offset in range(-3, 0):
            candidate = game_date + timedelta(days=offset)
            if candidate not in search_dates:
                search_dates.append(candidate)

    for search_date in search_dates:
        schedule = _load_schedule_payload(search_date, force_live=False)
        game = _game_from_payload(schedule, game_id)
        if game is None and not schedule.get("games"):
            schedule = _load_schedule_payload(search_date, force_live=True)
            game = _game_from_payload(schedule, game_id)
        if game is None:
            continue
        return {
            "date": schedule.get("date", search_date.isoformat()),
            "source": schedule.get("source"),
            "sport": "nhl",
            "game": game,
            "resolved_date": search_date.isoformat(),
            "requested_date": (game_date or today).isoformat(),
            "days_ahead": max(0, (search_date - today).days),
            "auto_advanced": search_date != (game_date or today),
        }
    return None
