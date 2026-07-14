"""Live NBA scores via ESPN scoreboard API (Phase D)."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

ESPN_NBA_SCOREBOARD = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
)
SCORES_CACHE_TTL_SECONDS = 45

_scores_cache: dict[str, Any] | None = None
_scores_cache_key: str | None = None
_scores_cache_at: datetime | None = None


def _espn_date_param(game_date: date) -> str:
    return game_date.strftime("%Y%m%d")


def fetch_nba_scores_day(game_date: date) -> list[dict[str, Any]]:
    params = {"dates": _espn_date_param(game_date)}
    with httpx.Client(timeout=30.0) as client:
        response = client.get(ESPN_NBA_SCOREBOARD, params=params)
        response.raise_for_status()
        data = response.json()
    return list(data.get("events") or [])


def _parse_score(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _nba_status(comp_status: dict[str, Any]) -> str:
    state = (comp_status.get("type") or {}).get("state", "")
    if state == "in":
        return "Live"
    if state == "post":
        return "Final"
    return "Preview"


def _nba_period_label(comp_status: dict[str, Any]) -> str | None:
    state = (comp_status.get("type") or {}).get("state", "")
    period = comp_status.get("period")
    clock = (comp_status.get("displayClock") or "").strip()
    if state == "in" and period:
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


def _series_summary(event: dict[str, Any], competition: dict[str, Any]) -> str | None:
    series = competition.get("series") or {}
    for key in ("summary", "title", "description"):
        value = series.get(key)
        if value:
            return str(value)

    notes = competition.get("notes") or []
    for note in notes:
        headline = note.get("headline") or note.get("text")
        if headline:
            return str(headline)

    event_name = event.get("name") or event.get("shortName") or ""
    if event_name and any(
        token in event_name.lower()
        for token in ("finals", "conference", "round", "game ", "semifinal", "play-in")
    ):
        return str(event_name)

    season = event.get("season") or {}
    season_type_raw = season.get("type")
    if isinstance(season_type_raw, dict):
        season_type = season_type_raw.get("name") or ""
    else:
        season_type = ""
    if season_type and season_type.lower() not in ("regular season", "preseason"):
        detail = (competition.get("status") or {}).get("type", {}).get("detail")
        if detail:
            return f"{season_type} · {detail}"
        return str(season_type)

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
    series_summary = _series_summary(event, competition)
    return {
        "sport": "nba",
        "game_id": str(event.get("id")),
        "home_team": home_team.get("displayName") or home_team.get("name") or "Home",
        "away_team": away_team.get("displayName") or away_team.get("name") or "Away",
        "home_team_id": int(home_id) if home_id is not None else None,
        "away_team_id": int(away_id) if away_id is not None else None,
        "home_team_abbr": home_team.get("abbreviation"),
        "away_team_abbr": away_team.get("abbreviation"),
        "home_logo_url": home_team.get("logo"),
        "away_logo_url": away_team.get("logo"),
        "home_record": _competitor_record(home),
        "away_record": _competitor_record(away),
        "series_summary": series_summary,
        "start_time_utc": event.get("date") or competition.get("date"),
        "status": _nba_status(status),
        "detailed_status": (status.get("type") or {}).get("description", ""),
        "period_label": _nba_period_label(status),
        "home_score": _parse_score(home.get("score")),
        "away_score": _parse_score(away.get("score")),
    }


def clear_scores_cache() -> None:
    global _scores_cache, _scores_cache_key, _scores_cache_at
    _scores_cache = None
    _scores_cache_key = None
    _scores_cache_at = None


def get_nba_scores_today(
    game_date: date | None = None,
    *,
    auto_resolve: bool = False,
) -> dict[str, Any]:
    requested_date = game_date or date.today()
    if auto_resolve and game_date is None:
        from app.services.schedule_nba import resolve_nba_slate_date

        resolved_date, days_ahead = resolve_nba_slate_date(None)
        auto_advanced = days_ahead > 0
    else:
        resolved_date = requested_date
        days_ahead = 0
        auto_advanced = False

    cache_key = f"nba:{resolved_date.isoformat()}"
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
        payload["no_games_this_week"] = bool(
            auto_resolve
            and game_date is None
            and int(payload.get("games_count") or 0) == 0
            and not auto_advanced
        )
        return payload

    events = fetch_nba_scores_day(resolved_date)
    games = [live_game_record(e) for e in events]

    payload: dict[str, Any] = {
        "sport": "nba",
        "date": resolved_date.isoformat(),
        "requested_date": requested_date.isoformat(),
        "resolved_date": resolved_date.isoformat(),
        "days_ahead": days_ahead,
        "auto_advanced": auto_advanced,
        "games": games,
        "games_count": len(games),
        "no_games_this_week": bool(
            auto_resolve and game_date is None and len(games) == 0 and not auto_advanced
        ),
        "cached_at": now.isoformat(),
        "cache_ttl_seconds": SCORES_CACHE_TTL_SECONDS,
        "source": "live",
        "cache_hit": False,
    }
    _scores_cache = payload
    _scores_cache_key = cache_key
    _scores_cache_at = now
    logger.debug("NBA scores refreshed: %s (%d games)", resolved_date, len(games))
    return payload
