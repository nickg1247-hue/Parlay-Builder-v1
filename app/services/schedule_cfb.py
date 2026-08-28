"""CFB schedule cache — ingest for past dates, ESPN API for today/future."""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from app.config import PROJECT_ROOT
from app.ingest.cfb_season_schedule import load_season_schedule
from app.odds.cfb_team_aliases import normalize_team_name
from app.services.cfb_game_metadata import annotate_game_metadata, game_identity, is_public_fbs_fcs_game, merge_game_records
from app.services.cfb_historical_slate import games_from_ingest, ingest_has_games
from app.services.cfb_team_logos import enrich_games_logos
from app.services.scores_cfb import (
    fetch_cfb_scores_day,
    fetch_espn_all_scores_day,
    fetch_lower_division_scores_day,
    live_game_record,
)
from app.services.slate_clock import slate_today

logger = logging.getLogger(__name__)

PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
SCHEDULE_CACHE_TTL_SECONDS = 6 * 3600
SCHEDULE_SCHEMA_VERSION = 5
SLATE_LOOKAHEAD_DAYS = 7


def _is_past_date(game_date: date) -> bool:
    return game_date < slate_today()


def resolve_cfb_slate_date(start: date | None = None) -> tuple[date, int]:
    """Pick slate date: start at *start* or today; if no games, try +1..+7 days."""
    anchor = start or slate_today()
    for offset in range(SLATE_LOOKAHEAD_DAYS + 1):
        candidate = anchor + timedelta(days=offset)
        if _date_has_games(candidate):
            return candidate, offset
    return anchor + timedelta(days=SLATE_LOOKAHEAD_DAYS), SLATE_LOOKAHEAD_DAYS


def _date_has_games(game_date: date) -> bool:
    path = schedule_cache_path(game_date)
    if path.exists():
        try:
            payload = _load_cache_payload(path)
            count = payload.get("games_count", len(payload.get("games") or []))
            if count > 0:
                return True
        except (json.JSONDecodeError, OSError):
            pass
    if _is_past_date(game_date):
        return ingest_has_games(game_date)
    try:
        if fetch_cfb_scores_day(game_date):
            return True
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("ESPN FBS date lookup unavailable for %s: %s", game_date, exc)
    season, week = _season_week_for_date(game_date)
    lower_games, _ = fetch_lower_division_scores_day(
        game_date,
        season=season,
        week=week,
    )
    return bool(lower_games)


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
    return PROCESSED_DIR / f"cfb_schedule_{game_date.isoformat()}.json"


def _cache_mtime_utc(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def cache_is_fresh(path: Path, ttl_seconds: int = SCHEDULE_CACHE_TTL_SECONDS) -> bool:
    if not path.exists():
        return False
    age = datetime.now(timezone.utc) - _cache_mtime_utc(path)
    return age.total_seconds() < ttl_seconds


def _should_read_cache(path: Path, *, force_live: bool) -> bool:
    """Reuse current-schema snapshots; preserve historical ingest caches."""
    if not path.exists() or force_live:
        return False
    try:
        payload = _load_cache_payload(path)
    except (json.JSONDecodeError, OSError):
        return False
    if payload.get("source") in (None, "ingest"):
        return True
    return int(payload.get("schema_version") or 0) >= SCHEDULE_SCHEMA_VERSION


def _is_empty_future_cache(path: Path, game_date: date) -> bool:
    if game_date < slate_today():
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


def _write_schedule_cache(
    game_date: date,
    games: list[dict[str, Any]],
    *,
    source: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cached_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "date": game_date.isoformat(),
        "sport": "cfb",
        "schema_version": SCHEDULE_SCHEMA_VERSION,
        "games": games,
        "games_count": len(games),
        "cached_at": cached_at,
        "source": source,
        **(metadata or {}),
    }
    path = schedule_cache_path(game_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info(
        "Wrote CFB schedule cache: %s (%d games, source=%s)",
        path.name,
        len(games),
        source,
    )
    return payload


def _season_week_for_date(game_date: date) -> tuple[int, int]:
    season = game_date.year
    schedule = load_season_schedule(season)
    exact = [
        int(row.get("week") or 0)
        for row in schedule
        if str(row.get("date") or "")[:10] == game_date.isoformat()
        and int(row.get("week") or 0) > 0
    ]
    if exact:
        return season, Counter(exact).most_common(1)[0][0]

    calendar_path = PROCESSED_DIR / "cfb_calendar_cache" / f"{season}.json"
    if calendar_path.exists():
        try:
            calendar = json.loads(calendar_path.read_text(encoding="utf-8"))
            target = game_date.isoformat()
            for row in calendar if isinstance(calendar, list) else []:
                if str(row.get("seasonType") or "").lower() != "regular":
                    continue
                start = str(row.get("startDate") or "")[:10]
                end = str(row.get("endDate") or "")[:10]
                if start <= target <= end:
                    return season, int(row.get("week") or 0)
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass
    return season, 0


def _annotate_fbs_schedule(
    games: list[dict[str, Any]],
    *,
    game_date: date,
) -> list[dict[str, Any]]:
    rows = load_season_schedule(game_date.year)
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if str(row.get("date") or "")[:10] != game_date.isoformat():
            continue
        home = normalize_team_name(str(row.get("home_team") or ""))
        away = normalize_team_name(str(row.get("away_team") or ""))
        index[(home, away)] = row
        index[(away, home)] = row

    out = []
    for raw in games:
        game = dict(raw)
        home = normalize_team_name(
            str(game.get("home_team_model_name") or game.get("home_team") or "")
        )
        away = normalize_team_name(
            str(game.get("away_team_model_name") or game.get("away_team") or "")
        )
        matched = index.get((home, away))
        if matched:
            same_orientation = normalize_team_name(
                str(matched.get("home_team") or "")
            ) == home
            for field in ("conference", "division"):
                home_value = matched.get(
                    f"{'home' if same_orientation else 'away'}_{field}"
                )
                away_value = matched.get(
                    f"{'away' if same_orientation else 'home'}_{field}"
                )
                if home_value:
                    game[f"home_{field}"] = home_value
                if away_value:
                    game[f"away_{field}"] = away_value
            divisions = matched.get("divisions") or []
            if divisions:
                game["divisions"] = list(divisions)
            game["conference_game"] = int(matched.get("conference_game") or 0)
        out.append(annotate_game_metadata(game))
    return out


def _load_schedule_payload(game_date: date, *, force_live: bool = False) -> dict[str, Any]:
    path = schedule_cache_path(game_date)
    if _should_read_cache(path, force_live=force_live):
        payload = _load_cache_payload(path)
        payload["source"] = payload.get("source", "cache")
        payload["games"] = [
            annotate_game_metadata(dict(game))
            for game in payload.get("games") or []
        ]
        if payload.get("source") == "ingest" or any(
            not (g.get("home_logo_url") or g.get("away_logo_url"))
            for g in payload.get("games") or []
        ):
            payload["games"] = enrich_games_logos(payload.get("games") or [])
        payload["games_count"] = len(payload["games"])
        return payload

    if _is_past_date(game_date):
        games = [
            annotate_game_metadata(dict(game))
            for game in games_from_ingest(game_date)
        ]
        source = "ingest"
        if not games:
            logger.info("No ingested CFB games for %s", game_date.isoformat())
        return _write_schedule_cache(game_date, games, source=source)

    coverage_warnings: list[str] = []
    try:
        events = fetch_cfb_scores_day(game_date)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("ESPN FBS schedule unavailable for %s: %s", game_date, exc)
        events = []
        coverage_warnings.append("ESPN FBS coverage unavailable.")
    fbs_games = _annotate_fbs_schedule(
        [live_game_record(event) for event in events],
        game_date=game_date,
    )
    season, week = _season_week_for_date(game_date)
    lower_games, lower_warnings = fetch_lower_division_scores_day(
        game_date,
        season=season,
        week=week,
    )
    try:
        espn_all=[live_game_record(event)for event in fetch_espn_all_scores_day(game_date)]
        espn_by_identity={game_identity(game):game for game in espn_all}
        for lower in lower_games:
            matched=espn_by_identity.get(game_identity(lower))
            if matched and matched.get("neutral_site_known"):
                lower["neutral_site"]=matched["neutral_site"]
                lower["neutral_site_known"]=True
                lower["neutral_site_missing"]=False
                lower["neutral_site_source"]="espn"
                for field in ("home_team_id","away_team_id","home_team_model_name","away_team_model_name"):
                    if matched.get(field)not in(None,""):lower[field]=matched[field]
    except (httpx.HTTPError,ValueError)as exc:
        logger.warning("ESPN all-CFB metadata unavailable for %s: %s",game_date,exc)
        coverage_warnings.append("ESPN neutral-site enrichment unavailable.")
    coverage_warnings.extend(lower_warnings)
    games = enrich_games_logos(merge_game_records(fbs_games + lower_games))
    divisions = sorted(
        {division for game in games for division in game.get("divisions") or []}
    )
    source_counts = {
        "espn_fbs": len(fbs_games),
        "espn_metadata": len(espn_all) if 'espn_all' in locals() else 0,
        "ncaa_fbs": sum(1 for game in lower_games if game.get("division") == "fbs"),
        "ncaa_fcs": sum(1 for game in lower_games if game.get("division") == "fcs"),
    }
    division_counts = {
        division: sum(1 for game in games if division in (game.get("divisions") or []))
        for division in ("fbs", "fcs")
    }
    logger.info(
        "CFB coverage %s: source_counts=%s division_counts=%s warnings=%s",
        game_date.isoformat(), source_counts, division_counts, coverage_warnings,
    )
    return _write_schedule_cache(
        game_date,
        games,
        source="espn+ncaa",
        metadata={
            "coverage_warnings": coverage_warnings,
            "coverage": {
                "complete": not coverage_warnings,
                "source_counts": source_counts,
                "division_counts": division_counts,
            },
            "divisions": divisions,
            "ncaa_week": week,
        },
    )


def _game_from_payload(payload: dict[str, Any], game_id: str) -> dict[str, Any] | None:
    return next(
        (g for g in payload.get("games", []) if str(g.get("game_id")) == str(game_id)),
        None,
    )


def refresh_schedule_cache(game_date: date | None = None) -> dict[str, Any]:
    game_date = game_date or slate_today()
    return _load_schedule_payload(game_date, force_live=True)


def _load_cache_payload(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def get_cfb_schedule(
    game_date: date | None = None,
    *,
    auto_resolve: bool = False,
    force_live: bool = False,
) -> dict[str, Any]:
    requested_date = game_date or slate_today()
    if auto_resolve and game_date is None:
        resolved_date, days_ahead = resolve_cfb_slate_date(None)
        auto_advanced = days_ahead > 0
    else:
        resolved_date = requested_date
        days_ahead = 0
        auto_advanced = False

    payload = _load_schedule_payload(resolved_date, force_live=force_live)
    payload["games"] = [
        game for game in payload.get("games") or []
        if is_public_fbs_fcs_game(game)
    ]
    payload["games_count"] = len(payload["games"])
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


def get_cfb_game(game_id: str, game_date: date | None = None) -> dict[str, Any] | None:
    if game_date is not None:
        return _find_game_in_schedule(game_id, game_date)

    resolved_date, _ = resolve_cfb_slate_date(None)
    search_dates: list[date] = [resolved_date]
    today = slate_today()
    for offset in range(SLATE_LOOKAHEAD_DAYS + 1):
        candidate = today + timedelta(days=offset)
        if candidate not in search_dates:
            search_dates.append(candidate)

    for search_date in search_dates:
        detail = _find_game_in_schedule(game_id, search_date)
        if detail is not None:
            if search_date == resolved_date:
                _, days_ahead = resolve_cfb_slate_date(None)
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


def _game_detail_from_schedule(
    game: dict[str, Any],
    schedule: dict[str, Any],
    game_date: date,
) -> dict[str, Any]:
    return {
        "date": schedule.get("date", game_date.isoformat()),
        "source": schedule.get("source"),
        "sport": "cfb",
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
        game = _game_from_payload(schedule, game_id)

    if game is None:
        return None
    schedule.setdefault("date", game_date.isoformat())
    schedule.setdefault("resolved_date", game_date.isoformat())
    schedule.setdefault("requested_date", game_date.isoformat())
    return _game_detail_from_schedule(game, schedule, game_date)
