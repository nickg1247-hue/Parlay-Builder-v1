"""NBA schedule cache for Phase D (ESPN scoreboard)."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT
from app.services.scores_nba import fetch_nba_scores_day, live_game_record

logger = logging.getLogger(__name__)

PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
SCHEDULE_CACHE_TTL_SECONDS = 6 * 3600
SLATE_LOOKAHEAD_DAYS = 3


def resolve_nba_slate_date(start: date | None = None) -> tuple[date, int]:
    """Pick slate date: start at *start* or today; if no games, try +1..+3 days."""
    anchor = start or date.today()
    for offset in range(SLATE_LOOKAHEAD_DAYS + 1):
        candidate = anchor + timedelta(days=offset)
        if fetch_nba_scores_day(candidate):
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
    return PROCESSED_DIR / f"nba_schedule_{game_date.isoformat()}.json"


def _cache_mtime_utc(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def cache_is_fresh(path: Path, ttl_seconds: int = SCHEDULE_CACHE_TTL_SECONDS) -> bool:
    if not path.exists():
        return False
    age = datetime.now(timezone.utc) - _cache_mtime_utc(path)
    return age.total_seconds() < ttl_seconds


def _is_empty_future_cache(path: Path, game_date: date) -> bool:
    """Empty cache for today or future dates is stale even within TTL."""
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


def _game_from_payload(payload: dict[str, Any], game_id: str) -> dict[str, Any] | None:
    return next(
        (g for g in payload.get("games", []) if str(g.get("game_id")) == str(game_id)),
        None,
    )


def refresh_schedule_cache(game_date: date | None = None) -> dict[str, Any]:
    """Fetch NBA scoreboard and write ``nba_schedule_{date}.json``."""
    game_date = game_date or date.today()
    events = fetch_nba_scores_day(game_date)
    games = [live_game_record(e) for e in events]
    cached_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "date": game_date.isoformat(),
        "sport": "nba",
        "games": games,
        "games_count": len(games),
        "cached_at": cached_at,
    }
    path = schedule_cache_path(game_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("Wrote NBA schedule cache: %s (%d games)", path.name, len(games))
    return payload


def _load_cache_payload(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def get_nba_schedule(
    game_date: date | None = None,
    *,
    auto_resolve: bool = False,
) -> dict[str, Any]:
    """Return schedule from cache if fresh (<6h), else fetch and write."""
    requested_date = game_date or date.today()
    if auto_resolve and game_date is None:
        resolved_date, days_ahead = resolve_nba_slate_date(None)
        auto_advanced = days_ahead > 0
    else:
        resolved_date = requested_date
        days_ahead = 0
        auto_advanced = False

    payload = _load_schedule_payload(resolved_date)

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


def _game_detail_from_schedule(
    game: dict[str, Any],
    schedule: dict[str, Any],
    game_date: date,
) -> dict[str, Any]:
    return {
        "date": schedule.get("date", game_date.isoformat()),
        "source": schedule.get("source"),
        "sport": "nba",
        "game": game,
        "resolved_date": schedule.get("resolved_date", game_date.isoformat()),
        "requested_date": schedule.get("requested_date", game_date.isoformat()),
        "days_ahead": schedule.get("days_ahead", 0),
        "auto_advanced": schedule.get("auto_advanced", False),
    }


def _find_game_in_schedule(game_id: str, game_date: date) -> dict[str, Any] | None:
    schedule = _load_schedule_payload(game_date)
    game = _game_from_payload(schedule, game_id)
    games_count = schedule.get("games_count", len(schedule.get("games") or []))

    if game is None or games_count == 0:
        schedule = refresh_schedule_cache(game_date)
        schedule["source"] = "api"
        game = _game_from_payload(schedule, game_id)

    if game is None:
        return None
    schedule.setdefault("date", game_date.isoformat())
    schedule.setdefault("resolved_date", game_date.isoformat())
    schedule.setdefault("requested_date", game_date.isoformat())
    return _game_detail_from_schedule(game, schedule, game_date)


def get_nba_game(game_id: str, game_date: date | None = None) -> dict[str, Any] | None:
    if game_date is not None:
        return _find_game_in_schedule(game_id, game_date)

    # No explicit date: resolved slate first, then today..+3 if not on resolved day.
    resolved_date, _ = resolve_nba_slate_date(None)
    search_dates: list[date] = [resolved_date]
    today = date.today()
    for offset in range(SLATE_LOOKAHEAD_DAYS + 1):
        candidate = today + timedelta(days=offset)
        if candidate not in search_dates:
            search_dates.append(candidate)

    for search_date in search_dates:
        detail = _find_game_in_schedule(game_id, search_date)
        if detail is not None:
            if search_date == resolved_date:
                _, days_ahead = resolve_nba_slate_date(None)
                detail.update(
                    _slate_meta(
                        requested_date=today,
                        resolved_date=resolved_date,
                        days_ahead=days_ahead,
                        auto_advanced=days_ahead > 0,
                    )
                )
            else:
                detail.update(
                    _slate_meta(
                        requested_date=today,
                        resolved_date=search_date,
                        days_ahead=(search_date - today).days,
                        auto_advanced=(search_date - today).days > 0,
                    )
                )
            return detail
    return None
