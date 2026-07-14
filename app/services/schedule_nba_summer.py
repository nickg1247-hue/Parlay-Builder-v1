"""NBA Summer League schedule cache (ESPN scoreboard)."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT
from app.services.scores_nba_summer import (
    fetch_nba_summer_scores_day,
    live_game_record,
)

logger = logging.getLogger(__name__)

PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
SCHEDULE_CACHE_TTL_SECONDS = 6 * 3600
SLATE_LOOKAHEAD_DAYS = 3


def resolve_nba_summer_slate_date(start: date | None = None) -> tuple[date, int]:
    """Pick slate date: start at *start* or today; if no games, try +1..+3 days."""
    anchor = start or date.today()
    for offset in range(SLATE_LOOKAHEAD_DAYS + 1):
        candidate = anchor + timedelta(days=offset)
        if fetch_nba_summer_scores_day(candidate):
            return candidate, offset
    return anchor + timedelta(days=SLATE_LOOKAHEAD_DAYS), SLATE_LOOKAHEAD_DAYS


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


def schedule_cache_path(game_date: date) -> Path:
    return PROCESSED_DIR / f"nba_summer_schedule_{game_date.isoformat()}.json"


def _cache_mtime_utc(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def cache_is_fresh(path: Path, ttl_seconds: int = SCHEDULE_CACHE_TTL_SECONDS) -> bool:
    if not path.exists():
        return False
    age = datetime.now(timezone.utc) - _cache_mtime_utc(path)
    return age.total_seconds() < ttl_seconds


def _is_empty_future_cache(path: Path, game_date: date) -> bool:
    if game_date < date.today():
        return False
    if not path.exists():
        return False
    try:
        payload = _load_cache_payload(path)
    except (json.JSONDecodeError, OSError):
        return False
    count = payload.get("games_count")
    if count is None:
        count = len(payload.get("games") or [])
    return count == 0


def _load_cache_payload(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def refresh_schedule_cache(game_date: date | None = None) -> dict[str, Any]:
    game_date = game_date or date.today()
    events = fetch_nba_summer_scores_day(game_date)
    games = [live_game_record(e) for e in events]
    cached_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "date": game_date.isoformat(),
        "sport": "nba-summer",
        "games": games,
        "games_count": len(games),
        "cached_at": cached_at,
    }
    path = schedule_cache_path(game_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info(
        "Wrote NBA Summer schedule cache: %s (%d games)", path.name, len(games)
    )
    return payload


def _load_schedule_payload(game_date: date, *, force_live: bool = False) -> dict[str, Any]:
    path = schedule_cache_path(game_date)
    if (
        not force_live
        and cache_is_fresh(path)
        and not _is_empty_future_cache(path, game_date)
    ):
        payload = _load_cache_payload(path)
        payload["source"] = "cache"
        return payload
    payload = refresh_schedule_cache(game_date)
    payload["source"] = "api"
    return payload


def get_nba_summer_schedule(
    game_date: date | None = None,
    *,
    auto_resolve: bool = False,
) -> dict[str, Any]:
    requested_date = game_date or date.today()
    if auto_resolve and game_date is None:
        resolved_date, days_ahead = resolve_nba_summer_slate_date(None)
        auto_advanced = days_ahead > 0
    else:
        resolved_date = requested_date
        days_ahead = 0
        auto_advanced = False

    payload = _load_schedule_payload(resolved_date)
    payload["date"] = resolved_date.isoformat()
    payload["sport"] = "nba-summer"
    payload.update(
        _slate_meta(
            requested_date=requested_date,
            resolved_date=resolved_date,
            days_ahead=days_ahead,
            auto_advanced=auto_advanced,
        )
    )
    return payload


def get_nba_summer_game(
    game_id: str,
    game_date: date | None = None,
) -> dict[str, Any] | None:
    """Lookup a Summer League game by ESPN event id."""
    dates_to_try: list[date] = []
    if game_date is not None:
        dates_to_try.append(game_date)
    else:
        today = date.today()
        for offset in range(-1, SLATE_LOOKAHEAD_DAYS + 1):
            dates_to_try.append(today + timedelta(days=offset))

    for d in dates_to_try:
        payload = _load_schedule_payload(d)
        for game in payload.get("games") or []:
            if str(game.get("game_id")) == str(game_id):
                return {"game": game, "date": d.isoformat(), "sport": "nba-summer"}
    return None
