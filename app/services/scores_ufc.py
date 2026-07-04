"""Live UFC scores via ESPN MMA scoreboard API."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

import httpx

from app.odds.ufc_fighter_aliases import normalize_fighter_name

logger = logging.getLogger(__name__)

ESPN_UFC_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard"
SCORES_CACHE_TTL_SECONDS = 45

_scores_cache: dict[str, Any] | None = None
_scores_cache_key: str | None = None
_scores_cache_at: datetime | None = None


def _espn_date_param(game_date: date) -> str:
    return game_date.strftime("%Y%m%d")


def fetch_ufc_scoreboard_day(game_date: date) -> list[dict[str, Any]]:
    params = {"dates": _espn_date_param(game_date)}
    with httpx.Client(timeout=30.0) as client:
        response = client.get(ESPN_UFC_SCOREBOARD, params=params)
        response.raise_for_status()
        data = response.json()
    return list(data.get("events") or [])


def _ufc_status(comp_status: dict[str, Any]) -> str:
    state = (comp_status.get("type") or {}).get("state", "")
    if state == "in":
        return "Live"
    if state == "post":
        return "Final"
    return "Preview"


def _fighter_name(competitor: dict[str, Any]) -> str:
    athlete = competitor.get("athlete") or {}
    raw = athlete.get("displayName") or athlete.get("shortName") or ""
    return normalize_fighter_name(str(raw))


def _competitor_record(competitor: dict[str, Any]) -> str | None:
    athlete = competitor.get("athlete") or {}
    for rec in athlete.get("record") or competitor.get("records") or []:
        summary = rec.get("summary") or rec.get("displayValue")
        if summary:
            return str(summary)
    return None


def _competitor_media(competitor: dict[str, Any]) -> dict[str, Any]:
    from app.services.ufc_fighter_media import media_from_competitor

    return media_from_competitor(competitor) or {}


def fight_record(event: dict[str, Any], competition: dict[str, Any]) -> dict[str, Any]:
    competitors = competition.get("competitors") or []
    by_order: dict[int, dict[str, Any]] = {}
    for c in competitors:
        order = c.get("order")
        if order is not None:
            by_order[int(order)] = c
    home = by_order.get(1) or competitors[0] if competitors else {}
    away = by_order.get(2) or (competitors[1] if len(competitors) > 1 else {})
    status = competition.get("status") or {}
    weight = (competition.get("type") or {}).get("text") or ""
    card_seg = competition.get("cardSegment")
    if isinstance(card_seg, dict):
        card_segment = str(card_seg.get("text") or "")
    else:
        card_segment = str(card_seg or "")

    home_name = _fighter_name(home)
    away_name = _fighter_name(away)
    home_won = bool(home.get("winner"))
    away_won = bool(away.get("winner"))
    home_media = _competitor_media(home)
    away_media = _competitor_media(away)

    return {
        "sport": "ufc",
        "game_id": str(competition.get("id") or ""),
        "fight_id": str(competition.get("id") or ""),
        "event_id": str(event.get("id") or ""),
        "event_name": event.get("name") or event.get("shortName") or "UFC",
        "home_team": home_name or "Fighter 1",
        "away_team": away_name or "Fighter 2",
        "home_fighter": home_name or "Fighter 1",
        "away_fighter": away_name or "Fighter 2",
        "home_record": _competitor_record(home),
        "away_record": _competitor_record(away),
        "home_athlete_id": home_media.get("athlete_id"),
        "away_athlete_id": away_media.get("athlete_id"),
        "home_headshot_url": home_media.get("headshot_url"),
        "away_headshot_url": away_media.get("headshot_url"),
        "home_flag_url": home_media.get("flag_url"),
        "away_flag_url": away_media.get("flag_url"),
        "home_flag_backdrop_url": home_media.get("flag_backdrop_url"),
        "away_flag_backdrop_url": away_media.get("flag_backdrop_url"),
        "home_country": home_media.get("country"),
        "away_country": away_media.get("country"),
        "home_country_code": home_media.get("country_code"),
        "away_country_code": away_media.get("country_code"),
        "home_logo_url": home_media.get("headshot_url"),
        "away_logo_url": away_media.get("headshot_url"),
        "weight_class": weight,
        "card_segment": card_segment,
        "start_time_utc": competition.get("date") or event.get("date"),
        "status": _ufc_status(status),
        "detailed_status": (status.get("type") or {}).get("description", ""),
        "home_winner": home_won,
        "away_winner": away_won,
        "winner": home_name if home_won else (away_name if away_won else None),
    }


def live_fights_from_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fights: list[dict[str, Any]] = []
    for event in events:
        for comp in event.get("competitions") or []:
            record = fight_record(event, comp)
            if record.get("game_id"):
                fights.append(record)
    return fights


def clear_scores_cache() -> None:
    global _scores_cache, _scores_cache_key, _scores_cache_at
    _scores_cache = None
    _scores_cache_key = None
    _scores_cache_at = None


def get_ufc_scores_today(
    game_date: date | None = None,
    *,
    auto_resolve: bool = False,
    force_live: bool = False,
) -> dict[str, Any]:
    from app.services.schedule_ufc import get_ufc_schedule

    requested_date = game_date or date.today()
    cache_key = f"ufc:{requested_date.isoformat()}:live={force_live}"
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

    schedule = get_ufc_schedule(
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
        "UFC scores: %s (%d fights, source=%s)",
        payload.get("resolved_date"),
        payload.get("games_count", 0),
        payload.get("source"),
    )
    return payload
