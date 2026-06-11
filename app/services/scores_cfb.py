"""Live CFB (FBS) scores — routes through schedule_cfb (ingest / cache / API)."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

ESPN_CFB_SCOREBOARD = (
    "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard"
)
FBS_GROUPS = "80"
SCORES_CACHE_TTL_SECONDS = 45

_scores_cache: dict[str, Any] | None = None
_scores_cache_key: str | None = None
_scores_cache_at: datetime | None = None


def _espn_date_param(game_date: date) -> str:
    return game_date.strftime("%Y%m%d")


def fetch_cfb_scores_day(game_date: date) -> list[dict[str, Any]]:
    params = {"dates": _espn_date_param(game_date), "groups": FBS_GROUPS}
    with httpx.Client(timeout=30.0) as client:
        response = client.get(ESPN_CFB_SCOREBOARD, params=params)
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


def _cfb_status(comp_status: dict[str, Any]) -> str:
    state = (comp_status.get("type") or {}).get("state", "")
    if state == "in":
        return "Live"
    if state == "post":
        return "Final"
    return "Preview"


def _cfb_period_label(comp_status: dict[str, Any]) -> str | None:
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
    return {
        "sport": "cfb",
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
        "start_time_utc": event.get("date") or competition.get("date"),
        "status": _cfb_status(status),
        "detailed_status": (status.get("type") or {}).get("description", ""),
        "period_label": _cfb_period_label(status),
        "home_score": _parse_score(home.get("score")),
        "away_score": _parse_score(away.get("score")),
    }


def clear_scores_cache() -> None:
    global _scores_cache, _scores_cache_key, _scores_cache_at
    _scores_cache = None
    _scores_cache_key = None
    _scores_cache_at = None


def get_cfb_scores_today(
    game_date: date | None = None,
    *,
    auto_resolve: bool = False,
    force_live: bool = False,
) -> dict[str, Any]:
    from app.services.schedule_cfb import get_cfb_schedule

    requested_date = game_date or date.today()
    cache_key = f"cfb:{requested_date.isoformat()}:live={force_live}"
    now = datetime.now(timezone.utc)

    global _scores_cache, _scores_cache_key, _scores_cache_at
    if (
        not force_live
        and _scores_cache is not None
        and _scores_cache_key == cache_key
        and _scores_cache_at is not None
        and (now - _scores_cache_at).total_seconds() < SCORES_CACHE_TTL_SECONDS
    ):
        return {**_scores_cache, "cache_hit": True}

    schedule = get_cfb_schedule(
        game_date,
        auto_resolve=auto_resolve,
        force_live=force_live,
    )
    payload: dict[str, Any] = {
        **schedule,
        "cache_hit": schedule.get("source") in ("cache", "ingest"),
        "cache_ttl_seconds": SCORES_CACHE_TTL_SECONDS,
    }
    if not force_live:
        _scores_cache = payload
        _scores_cache_key = cache_key
        _scores_cache_at = now
    logger.debug(
        "CFB scores: %s (%d games, source=%s)",
        payload.get("resolved_date"),
        payload.get("games_count", 0),
        payload.get("source"),
    )
    return payload
