"""Live NFL scores via ESPN scoreboard API."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

import httpx

from app.ingest.nfl import ESPN_NFL_SCOREBOARD, normalize_abbr
from app.services.slate_clock import slate_today

logger = logging.getLogger(__name__)

SCORES_CACHE_TTL_SECONDS = 45

_scores_cache: dict[str, Any] | None = None
_scores_cache_key: str | None = None
_scores_cache_at: datetime | None = None


def _espn_date_param(game_date: date) -> str:
    return game_date.strftime("%Y%m%d")


def fetch_nfl_scores_day(game_date: date) -> list[dict[str, Any]]:
    params = {"dates": _espn_date_param(game_date)}
    try:
        with httpx.Client(timeout=6.0) as client:
            response = client.get(ESPN_NFL_SCOREBOARD, params=params)
            response.raise_for_status()
            data = response.json()
        return list(data.get("events") or [])
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("ESPN NFL scoreboard failed for %s: %s", game_date.isoformat(), exc)
        return []


def _parse_score(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _nfl_status(comp_status: dict[str, Any]) -> str:
    state = (comp_status.get("type") or {}).get("state", "")
    if state == "in":
        return "Live"
    if state == "post":
        return "Final"
    return "Preview"


def _nfl_period_label(comp_status: dict[str, Any]) -> str | None:
    state = (comp_status.get("type") or {}).get("state", "")
    period = comp_status.get("period")
    clock = (comp_status.get("displayClock") or "").strip()
    if state == "in" and period:
        if period > 4:
            label = "OT"
        else:
            label = f"Q{period}"
        if clock and clock not in ("0.0", "0:00", "0"):
            label = f"{label} {clock}"
        return label
    short = (comp_status.get("type") or {}).get("shortDetail") or ""
    if short and short.lower() not in ("scheduled", "pre-game"):
        return short
    return None


def _competitor_record(competitor: dict[str, Any]) -> str | None:
    records = competitor.get("records") or []
    for rec in records:
        name = (rec.get("name") or rec.get("type") or "").lower()
        summary = rec.get("summary")
        if summary and name in ("overall", "total", "ytd"):
            return str(summary)
    for rec in records:
        summary = rec.get("summary")
        if summary:
            return str(summary)
    return None


def live_game_record(event: dict[str, Any]) -> dict[str, Any]:
    competition = (event.get("competitions") or [{}])[0]
    competitors = competition.get("competitors") or []
    home = next((c for c in competitors if c.get("homeAway") == "home"), {})
    away = next((c for c in competitors if c.get("homeAway") == "away"), {})
    home_team = home.get("team") or {}
    away_team = away.get("team") or {}
    status = competition.get("status") or {}
    home_id = home_team.get("id")
    away_id = away_team.get("id")
    season = event.get("season") or {}
    week = event.get("week") or {}
    from app.ingest.nfl import _parse_espn_odds

    espn_odds = _parse_espn_odds(competition)
    return {
        "sport": "nfl",
        "game_id": str(event.get("id")),
        "home_team": home_team.get("displayName") or home_team.get("name") or "Home",
        "away_team": away_team.get("displayName") or away_team.get("name") or "Away",
        "home_team_id": str(home_id) if home_id is not None else "",
        "away_team_id": str(away_id) if away_id is not None else "",
        "home_team_abbr": normalize_abbr(home_team.get("abbreviation")),
        "away_team_abbr": normalize_abbr(away_team.get("abbreviation")),
        "home_logo_url": home_team.get("logo"),
        "away_logo_url": away_team.get("logo"),
        "home_record": _competitor_record(home),
        "away_record": _competitor_record(away),
        "start_time_utc": event.get("date") or competition.get("date"),
        "status": _nfl_status(status),
        "detailed_status": (status.get("type") or {}).get("description", ""),
        "period_label": _nfl_period_label(status),
        "home_score": _parse_score(home.get("score")),
        "away_score": _parse_score(away.get("score")),
        "season": season.get("year"),
        "week": week.get("number") if isinstance(week, dict) else None,
        "neutral_site": 1 if competition.get("neutralSite") else 0,
        "game_type": "preseason" if season.get("type") == 1 else "regular",
        "is_preseason": int(season.get("type") == 1),
        "espn_home_ml": espn_odds.get("espn_home_ml"),
        "espn_away_ml": espn_odds.get("espn_away_ml"),
        "espn_spread": espn_odds.get("espn_spread"),
        "espn_ou": espn_odds.get("espn_ou"),
    }


def clear_scores_cache() -> None:
    global _scores_cache, _scores_cache_key, _scores_cache_at
    _scores_cache = None
    _scores_cache_key = None
    _scores_cache_at = None


def get_nfl_scores_today(
    game_date: date | None = None,
    *,
    auto_resolve: bool = False,
    force_live: bool = False,
) -> dict[str, Any]:
    from app.services.schedule_nfl import get_nfl_schedule

    requested_date = game_date or slate_today()
    cache_key = f"nfl:{requested_date.isoformat()}:ar={int(auto_resolve)}"
    now = datetime.now(timezone.utc)

    global _scores_cache, _scores_cache_key, _scores_cache_at
    if (
        _scores_cache is not None
        and _scores_cache_key == cache_key
        and _scores_cache_at is not None
        and (now - _scores_cache_at).total_seconds() < SCORES_CACHE_TTL_SECONDS
    ):
        return {**_scores_cache, "cache_hit": True}

    schedule = get_nfl_schedule(
        game_date,
        auto_resolve=auto_resolve,
        force_live=force_live,
    )
    payload: dict[str, Any] = {
        **schedule,
        "cache_hit": schedule.get("source") in ("cache", "ingest"),
        "cache_ttl_seconds": SCORES_CACHE_TTL_SECONDS,
    }
    _scores_cache = payload
    _scores_cache_key = cache_key
    _scores_cache_at = now
    logger.debug(
        "NFL scores: %s (%d games, source=%s)",
        payload.get("resolved_date"),
        payload.get("games_count", 0),
        payload.get("source"),
    )
    return payload
