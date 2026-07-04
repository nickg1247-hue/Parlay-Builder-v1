"""UFC slate from ingest (past) or ESPN scoreboard (today/future)."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from app.config import PROJECT_ROOT
from app.services.scores_ufc import fetch_ufc_scoreboard_day, live_fights_from_events
from app.services.ufc_historical_slate import fights_from_ingest, ingest_has_fights

logger = logging.getLogger(__name__)

PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
SCHEDULE_CACHE_TTL_SECONDS = 6 * 3600
SLATE_LOOKAHEAD_DAYS = 30


def _is_past_date(game_date: date) -> bool:
    return game_date < date.today()


def schedule_cache_path(game_date: date) -> Path:
    return PROCESSED_DIR / f"ufc_schedule_{game_date.isoformat()}.json"


def _date_has_fights(game_date: date) -> bool:
    path = schedule_cache_path(game_date)
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            count = payload.get("games_count", len(payload.get("games") or []))
            if count > 0:
                return True
        except (json.JSONDecodeError, OSError):
            pass
    if _is_past_date(game_date):
        return ingest_has_fights(game_date)
    return bool(live_fights_from_events(fetch_ufc_scoreboard_day(game_date)))


def resolve_ufc_slate_date(start: date | None = None) -> tuple[date, int]:
    anchor = start or date.today()
    for offset in range(SLATE_LOOKAHEAD_DAYS + 1):
        candidate = anchor + timedelta(days=offset)
        if _date_has_fights(candidate):
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


def _write_schedule_cache(
    game_date: date,
    games: list[dict[str, Any]],
    *,
    source: str,
) -> dict[str, Any]:
    event_names = sorted({g.get("event_name") for g in games if g.get("event_name")})
    cached_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "date": game_date.isoformat(),
        "sport": "ufc",
        "games": games,
        "games_count": len(games),
        "events": event_names,
        "cached_at": cached_at,
        "source": source,
    }
    path = schedule_cache_path(game_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info(
        "Wrote UFC schedule cache: %s (%d fights, source=%s)",
        path.name,
        len(games),
        source,
    )
    return payload


def _load_schedule_payload(game_date: date, *, force_live: bool = False) -> dict[str, Any]:
    path = schedule_cache_path(game_date)
    if path.exists() and not force_live:
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["source"] = payload.get("source", "cache")
        return payload

    if _is_past_date(game_date):
        games = fights_from_ingest(game_date)
        return _write_schedule_cache(game_date, games, source="ingest")

    events = fetch_ufc_scoreboard_day(game_date)
    games = live_fights_from_events(events)
    return _write_schedule_cache(game_date, games, source="api")


def refresh_schedule_cache(game_date: date | None = None) -> dict[str, Any]:
    game_date = game_date or date.today()
    return _load_schedule_payload(game_date, force_live=True)


def _fight_from_payload(payload: dict[str, Any], fight_id: str) -> dict[str, Any] | None:
    fid = str(fight_id)
    for fight in payload.get("games") or []:
        if str(fight.get("fight_id") or fight.get("game_id")) == fid:
            return fight
    return None


def _fight_detail_from_schedule(
    fight: dict[str, Any],
    schedule: dict[str, Any],
    game_date: date,
) -> dict[str, Any]:
    return {
        "date": schedule.get("date", game_date.isoformat()),
        "source": schedule.get("source"),
        "sport": "ufc",
        "game": fight,
        "resolved_date": schedule.get("resolved_date", game_date.isoformat()),
        "requested_date": schedule.get("requested_date", game_date.isoformat()),
        "days_ahead": schedule.get("days_ahead", 0),
        "auto_advanced": schedule.get("auto_advanced", False),
    }


def _resolve_fight_date_from_ingest(fight_id: str) -> date | None:
    try:
        from app.models.ufc_baseline import load_fights

        fights = load_fights()
        rows = fights[fights["fight_id"].astype(str) == str(fight_id)]
        if rows.empty:
            return None
        return pd.to_datetime(rows.iloc[0]["date"]).date()
    except (FileNotFoundError, OSError, ValueError, KeyError):
        return None


def _find_fight_in_schedule(fight_id: str, game_date: date) -> dict[str, Any] | None:
    schedule = _load_schedule_payload(game_date)
    fight = _fight_from_payload(schedule, fight_id)
    games_count = schedule.get("games_count", len(schedule.get("games") or []))

    if fight is None or games_count == 0:
        schedule = refresh_schedule_cache(game_date)
        fight = _fight_from_payload(schedule, fight_id)

    if fight is None:
        return None
    schedule.setdefault("date", game_date.isoformat())
    schedule.setdefault("resolved_date", game_date.isoformat())
    schedule.setdefault("requested_date", game_date.isoformat())
    return _fight_detail_from_schedule(fight, schedule, game_date)


def get_ufc_fight(fight_id: str, game_date: date | None = None) -> dict[str, Any] | None:
    if game_date is not None:
        return _find_fight_in_schedule(fight_id, game_date)

    ingest_date = _resolve_fight_date_from_ingest(fight_id)
    if ingest_date is not None:
        detail = _find_fight_in_schedule(fight_id, ingest_date)
        if detail is not None:
            return detail

    resolved_date, _ = resolve_ufc_slate_date(None)
    search_dates: list[date] = [resolved_date]
    today = date.today()
    for offset in range(SLATE_LOOKAHEAD_DAYS + 1):
        candidate = today + timedelta(days=offset)
        if candidate not in search_dates:
            search_dates.append(candidate)

    for search_date in search_dates:
        detail = _find_fight_in_schedule(fight_id, search_date)
        if detail is not None:
            if search_date == resolved_date:
                _, days_ahead = resolve_ufc_slate_date(None)
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


def get_ufc_schedule(
    game_date: date | None = None,
    *,
    auto_resolve: bool = False,
    force_live: bool = False,
) -> dict[str, Any]:
    requested_date = game_date or date.today()
    if auto_resolve and game_date is None:
        resolved_date, days_ahead = resolve_ufc_slate_date(None)
        auto_advanced = days_ahead > 0
    else:
        resolved_date = requested_date
        days_ahead = 0
        auto_advanced = False

    payload = _load_schedule_payload(resolved_date, force_live=force_live)
    payload["date"] = resolved_date.isoformat()
    try:
        from app.services.ufc_fighter_media import enrich_fight_media

        payload["games"] = [
            enrich_fight_media(g, resolved_date) for g in (payload.get("games") or [])
        ]
    except Exception as exc:
        logger.warning("UFC media enrich skipped: %s", exc)
    payload.update(
        _slate_meta(
            requested_date=requested_date,
            resolved_date=resolved_date,
            days_ahead=days_ahead,
            auto_advanced=auto_advanced,
        )
    )
    return payload
