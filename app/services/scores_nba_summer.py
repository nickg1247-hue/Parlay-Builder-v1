"""Live NBA Summer League scores via ESPN scoreboard API."""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone
from typing import Any

import httpx

from app.services.scores_nba import (
    live_game_record as _nba_live_game_record,
)

logger = logging.getLogger(__name__)

# Las Vegas is the main Summer League. California (and others) are optional extras.
DEFAULT_LEAGUES = ("nba-summer-las-vegas",)
OPTIONAL_LEAGUES = ("nba-summer-california", "nba-summer-utah")

ESPN_SUMMER_BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball"
SCORES_CACHE_TTL_SECONDS = 45

_scores_cache: dict[str, Any] | None = None
_scores_cache_key: str | None = None
_scores_cache_at: datetime | None = None


def summer_enabled() -> bool:
    raw = os.getenv("NBA_SUMMER_ENABLED", "true").strip().lower()
    return raw not in ("0", "false", "no", "off")


def summer_leagues() -> tuple[str, ...]:
    raw = os.getenv("NBA_SUMMER_ESPN_LEAGUES", "").strip()
    if raw:
        return tuple(x.strip() for x in raw.split(",") if x.strip())
    leagues = list(DEFAULT_LEAGUES)
    if os.getenv("NBA_SUMMER_INCLUDE_OPTIONAL_LEAGUES", "false").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        leagues.extend(OPTIONAL_LEAGUES)
    return tuple(leagues)


def _espn_date_param(game_date: date) -> str:
    return game_date.strftime("%Y%m%d")


def fetch_nba_summer_scores_day(game_date: date) -> list[dict[str, Any]]:
    """Fetch Summer League events for a date across configured ESPN league slugs."""
    params = {"dates": _espn_date_param(game_date)}
    seen_ids: set[str] = set()
    events: list[dict[str, Any]] = []
    with httpx.Client(timeout=30.0) as client:
        for league in summer_leagues():
            url = f"{ESPN_SUMMER_BASE}/{league}/scoreboard"
            try:
                response = client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
            except Exception as exc:
                logger.warning("NBA Summer ESPN fetch failed (%s): %s", league, exc)
                continue
            for event in data.get("events") or []:
                eid = str(event.get("id") or "")
                if not eid or eid in seen_ids:
                    continue
                seen_ids.add(eid)
                event["_summer_league"] = league
                events.append(event)
    events.sort(key=lambda e: e.get("date") or "")
    return events


def live_game_record(event: dict[str, Any]) -> dict[str, Any]:
    row = _nba_live_game_record(event)
    row["sport"] = "nba-summer"
    league = event.get("_summer_league")
    if league:
        row["summer_league"] = league
        if "vegas" in str(league):
            row["series_summary"] = row.get("series_summary") or "Las Vegas Summer League"
        elif "california" in str(league):
            row["series_summary"] = row.get("series_summary") or "California Classic"
        elif "utah" in str(league):
            row["series_summary"] = row.get("series_summary") or "Salt Lake City Summer League"
    else:
        row["series_summary"] = row.get("series_summary") or "NBA Summer League"
    return row


def clear_scores_cache() -> None:
    global _scores_cache, _scores_cache_key, _scores_cache_at
    _scores_cache = None
    _scores_cache_key = None
    _scores_cache_at = None


def get_nba_summer_scores_today(
    game_date: date | None = None,
    *,
    auto_resolve: bool = False,
) -> dict[str, Any]:
    requested_date = game_date or date.today()
    if auto_resolve and game_date is None:
        from app.services.schedule_nba_summer import resolve_nba_summer_slate_date

        resolved_date, days_ahead = resolve_nba_summer_slate_date(None)
        auto_advanced = days_ahead > 0
    else:
        resolved_date = requested_date
        days_ahead = 0
        auto_advanced = False

    cache_key = f"nba-summer:{resolved_date.isoformat()}"
    now = datetime.now(timezone.utc)

    global _scores_cache, _scores_cache_key, _scores_cache_at
    if (
        _scores_cache is not None
        and _scores_cache_key == cache_key
        and _scores_cache_at is not None
        and (now - _scores_cache_at).total_seconds() < SCORES_CACHE_TTL_SECONDS
    ):
        payload = {**_scores_cache, "cache_hit": True}
        payload["requested_date"] = requested_date.isoformat()
        payload["resolved_date"] = resolved_date.isoformat()
        payload["days_ahead"] = days_ahead
        payload["auto_advanced"] = auto_advanced
        return payload

    events = fetch_nba_summer_scores_day(resolved_date)
    games = [live_game_record(e) for e in events]

    payload: dict[str, Any] = {
        "sport": "nba-summer",
        "date": resolved_date.isoformat(),
        "requested_date": requested_date.isoformat(),
        "resolved_date": resolved_date.isoformat(),
        "days_ahead": days_ahead,
        "auto_advanced": auto_advanced,
        "games": games,
        "games_count": len(games),
        "cached_at": now.isoformat(),
        "cache_ttl_seconds": SCORES_CACHE_TTL_SECONDS,
        "source": "live",
        "cache_hit": False,
    }
    _scores_cache = payload
    _scores_cache_key = cache_key
    _scores_cache_at = now
    logger.debug(
        "NBA Summer scores refreshed: %s (%d games)", resolved_date, len(games)
    )
    return payload
