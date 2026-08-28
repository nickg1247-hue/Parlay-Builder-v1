"""Live CFB (FBS) scores — routes through schedule_cfb (ingest / cache / API)."""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.services.cfb_game_metadata import annotate_game_metadata, game_identity
from app.services.slate_clock import slate_today

logger = logging.getLogger(__name__)

ESPN_CFB_SCOREBOARD = (
    "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard"
)
FBS_GROUPS = "80"
NCAA_SCOREBOARD = "https://ncaa-api.henrygd.me/scoreboard/football/{division}/{season}/{week:02d}/all-conf"
NCAA_DIVISIONS = ("fbs", "fcs")
NCAA_OWNERSHIP_DIVISIONS = ("d2", "d3")
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

def fetch_espn_all_scores_day(game_date:date)->list[dict[str,Any]]:
    """Full ESPN college-football slate used only as a metadata enrichment source."""
    params={"dates":_espn_date_param(game_date),"groups":"81"}
    with httpx.Client(timeout=30.0)as client:
        response=client.get(ESPN_CFB_SCOREBOARD,params=params);response.raise_for_status();data=response.json()
    return list(data.get("events")or[])


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
        "home_team_model_name": home_team.get("location") or home_team.get("shortDisplayName"),
        "away_team_model_name": away_team.get("location") or away_team.get("shortDisplayName"),
        "home_team_id": int(home_id) if home_id is not None else None,
        "away_team_id": int(away_id) if away_id is not None else None,
        "home_team_abbr": home_team.get("abbreviation"),
        "away_team_abbr": away_team.get("abbreviation"),
        "home_logo_url": home_team.get("logo"),
        "away_logo_url": away_team.get("logo"),
        "home_record": _competitor_record(home),
        "away_record": _competitor_record(away),
        "neutral_site": int(bool(competition.get("neutralSite"))),
        "neutral_site_known": "neutralSite" in competition,
        "neutral_site_source": "espn",
        "week": int((event.get("week") or {}).get("number") or 0),
        "start_time_utc": event.get("date") or competition.get("date"),
        "status": _cfb_status(status),
        "detailed_status": (status.get("type") or {}).get("description", ""),
        "period_label": _cfb_period_label(status),
        "home_score": _parse_score(home.get("score")),
        "away_score": _parse_score(away.get("score")),
    }


def _ncaa_side(game: dict[str, Any], side: str) -> dict[str, Any]:
    value = game.get(side) or game.get(f"{side}Team") or {}
    return value if isinstance(value, dict) else {}


def _ncaa_team_name(team: dict[str, Any], fallback: str) -> str:
    names = team.get("names") or {}
    return str(
        team.get("name")
        or team.get("displayName")
        or names.get("short")
        or names.get("full")
        or fallback
    )


_CONFERENCE_LABELS = {
    "big-sky": "Big Sky", "big-south-ovc": "Big South-OVC", "caa": "CAA",
    "ciaa": "CIAA", "ivy": "Ivy League", "meac": "MEAC", "mvfc": "MVFC",
    "nec": "NEC", "ovc": "OVC", "patriot": "Patriot League",
    "pioneer": "Pioneer Football League", "siac": "SIAC", "socon": "SoCon",
    "southland": "Southland", "swac": "SWAC", "uac": "UAC",
}


def _conference_label(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    return _CONFERENCE_LABELS.get(raw.lower(), raw.replace("-", " ").title())


def _ncaa_conference(team: dict[str, Any]) -> str | None:
    conference = _conference_label(team.get("conference"))
    if conference:
        return conference
    conferences = team.get("conferences") or []
    if conferences:
        first = conferences[0]
        if isinstance(first, dict):
            return _conference_label(
                first.get("conferenceName") or first.get("conferenceSeo")
                or first.get("name") or first.get("shortName")
            )
        return _conference_label(first)
    return None


def _ncaa_rank(team: dict[str, Any]) -> int | None:
    value = team.get("rank") or team.get("seed")
    try:
        rank = int(value)
    except (TypeError, ValueError):
        return None
    return rank if 1 <= rank <= 25 else None


def _ncaa_game_date(game: dict[str, Any]) -> str | None:
    raw = game.get("startDate") or game.get("date")
    if not raw:
        return None
    text = str(raw).strip()
    if "T" in text and len(text) >= 10:
        return text[:10]
    try:
        return datetime.strptime(text[:10], "%m/%d/%Y").date().isoformat()
    except ValueError:
        return text[:10]

def _ncaa_start_time(game: dict[str, Any]) -> str | None:
    epoch = game.get("startTimeEpoch")
    if epoch not in (None, ""):
        try:
            return datetime.fromtimestamp(int(epoch), tz=timezone.utc).isoformat().replace("+00:00", "Z")
        except (TypeError, ValueError, OSError):
            pass
    raw = game.get("start_time_utc") or game.get("startDate") or game.get("date")
    if not raw:
        return None
    text = str(raw).strip()
    if "T" in text:
        return text
    try:
        day = datetime.strptime(text[:10], "%m/%d/%Y").date()
    except ValueError:
        return text
    clock = str(game.get("startTime") or "12:00 AM ET").replace(" ET", "").strip()
    try:
        local_time = datetime.strptime(clock, "%I:%M %p").time()
    except ValueError:
        local_time = time(12, 0)
    local = datetime.combine(day, local_time, tzinfo=ZoneInfo("America/New_York"))
    return local.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _ncaa_status(game: dict[str, Any]) -> str:
    state = str(game.get("gameState") or game.get("status") or "pre").lower()
    if state in {"live", "in", "in_progress"}:
        return "Live"
    if state in {"final", "post", "completed"}:
        return "Final"
    return "Preview"

def ncaa_game_record(raw: dict[str, Any], division: str) -> dict[str, Any]:
    """Normalize one NCAA lower-division scoreboard game."""
    game = raw.get("game") if isinstance(raw.get("game"), dict) else raw
    home = _ncaa_side(game, "home")
    away = _ncaa_side(game, "away")
    start = _ncaa_start_time(game)
    record = {
        "sport": "cfb",
        "game_id": str(game.get("game_id") or game.get("gameID") or game.get("id") or ""),
        "home_team": _ncaa_team_name(home, "Home"),
        "away_team": _ncaa_team_name(away, "Away"),
        "home_team_id": home.get("id"),
        "away_team_id": away.get("id"),
        "home_logo_url": home.get("logo") or home.get("logoUrl"),
        "away_logo_url": away.get("logo") or away.get("logoUrl"),
        "home_conference": _ncaa_conference(home),
        "away_conference": _ncaa_conference(away),
        "home_rank": _ncaa_rank(home),
        "away_rank": _ncaa_rank(away),
        "home_score": _parse_score(home.get("score")),
        "away_score": _parse_score(away.get("score")),
        "date": _ncaa_game_date(game),
        "start_time_utc": start,
        "status": _ncaa_status(game),
        "detailed_status": game.get("finalMessage") or game.get("currentPeriod") or "",
        "period_label": game.get("currentPeriod") or None,
        "network": game.get("network"),
        "division": division,
        "divisions": [division],
        "source": "ncaa",
        "sources": ["ncaa"],
        "neutral_site_missing": True,
        "neutral_site_known": False,
    }
    return annotate_game_metadata(record)


def _fallback_ncaa_week(game_date: date) -> int:
    september_first = date(game_date.year, 9, 1)
    labor_day = september_first
    while labor_day.weekday() != 0:
        labor_day = labor_day.replace(day=labor_day.day + 1)
    if game_date < labor_day:
        return 1
    return 2 + max(0, (game_date - labor_day).days // 7)


def fetch_lower_division_scores_day(
    game_date: date,
    *,
    season: int,
    week: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Fetch every real NCAA FBS/FCS/D-II/D-III game for a calendar date."""
    resolved_week = week if week > 0 else _fallback_ncaa_week(game_date)
    candidate_weeks = sorted({max(0, resolved_week - 1), resolved_week, resolved_week + 1})
    games_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    warnings: list[str] = []
    successful_divisions: set[str] = set()
    with httpx.Client(timeout=30.0) as client:
        lower_owned_ids: set[str] = set()
        lower_owned_matchups: set[tuple[str, tuple[str, str]]] = set()
        lower_owned_teams: set[str] = set()
        for division in NCAA_DIVISIONS + NCAA_OWNERSHIP_DIVISIONS:
            for candidate_week in candidate_weeks:
                url = NCAA_SCOREBOARD.format(
                    division=division,
                    season=season,
                    week=candidate_week,
                )
                try:
                    response = client.get(url)
                    response.raise_for_status()
                    payload = response.json()
                    successful_divisions.add(division)
                except (httpx.HTTPError, ValueError) as exc:
                    logger.warning(
                        "NCAA %s week %s scoreboard failed: %s",
                        division,
                        candidate_week,
                        exc,
                    )
                    continue
                for raw in payload.get("games") or []:
                    record = ncaa_game_record(raw, division)
                    if record.get("date") != game_date.isoformat():
                        continue
                    game_id = str(record.get("game_id") or "")
                    if division in NCAA_OWNERSHIP_DIVISIONS:
                        lower_owned_ids.add(game_id)
                        lower_owned_matchups.add(game_identity(record))
                        lower_owned_teams.update(game_identity(record)[1])
                    else:
                        games_by_key[(division, game_id)] = record
        games_by_key = {
            key: record for key, record in games_by_key.items()
            if not (key[0] == "fcs" and (key[1] in lower_owned_ids or game_identity(record) in lower_owned_matchups or bool(set(game_identity(record)[1]) & lower_owned_teams)))
        }
    for division in NCAA_DIVISIONS:
        if division not in successful_divisions:
            warnings.append(f"NCAA {division.upper()} coverage unavailable.")
    return list(games_by_key.values()), warnings


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

    requested_date = game_date or slate_today()
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
