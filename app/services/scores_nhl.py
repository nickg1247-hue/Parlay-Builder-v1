"""Live NHL scores via the ESPN scoreboard API."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

import httpx

from app.services.nhl_ratings import canonical_abbr
from app.services.slate_clock import slate_today

logger = logging.getLogger(__name__)

ESPN_NHL_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard"
SCORES_CACHE_TTL_SECONDS = 45

_scores_cache: dict[str, Any] | None = None
_scores_cache_key: str | None = None
_scores_cache_at: datetime | None = None


def _espn_date_param(game_date: date) -> str:
    return game_date.strftime("%Y%m%d")


def fetch_nhl_scores_day(game_date: date) -> list[dict[str, Any]]:
    try:
        with httpx.Client(timeout=8.0, follow_redirects=True) as client:
            response = client.get(ESPN_NHL_SCOREBOARD, params={"dates": _espn_date_param(game_date)})
            response.raise_for_status()
            data = response.json()
        return list(data.get("events") or [])
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("ESPN NHL scoreboard failed for %s: %s", game_date.isoformat(), exc)
        return []


def _parse_score(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _american(value: Any) -> int | None:
    if value is None or value == "":
        return None
    text = str(value).replace("+", "").strip()
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    text = str(value).replace("+", "").strip()
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def parse_espn_nhl_odds(competition: dict[str, Any]) -> dict[str, Any]:
    odds_list = competition.get("odds") or []
    if not odds_list or not isinstance(odds_list[0], dict):
        return {}
    blob = odds_list[0]
    moneyline = blob.get("moneyline") or {}
    home_ml = _american(((moneyline.get("home") or {}).get("close") or {}).get("odds"))
    away_ml = _american(((moneyline.get("away") or {}).get("close") or {}).get("odds"))
    if home_ml is None:
        home_ml = _american((blob.get("homeTeamOdds") or {}).get("moneyLine"))
    if away_ml is None:
        away_ml = _american((blob.get("awayTeamOdds") or {}).get("moneyLine"))

    spread = _float(blob.get("spread"))
    point_spread = blob.get("pointSpread") or {}
    home_line = _float(((point_spread.get("home") or {}).get("close") or {}).get("line"))
    if home_line is not None:
        spread = home_line

    ou = _float(blob.get("overUnder"))
    total = blob.get("total") or {}
    total_line = _float(((total.get("over") or {}).get("close") or {}).get("line"))
    if total_line is not None:
        ou = total_line
    return {
        "espn_home_ml": home_ml,
        "espn_away_ml": away_ml,
        "espn_spread": spread,
        "espn_ou": ou,
    }


def _status(comp_status: dict[str, Any]) -> str:
    state = (comp_status.get("type") or {}).get("state", "")
    if state == "in":
        return "Live"
    if state == "post":
        return "Final"
    return "Preview"


def _period_label(comp_status: dict[str, Any]) -> str | None:
    state = (comp_status.get("type") or {}).get("state", "")
    period = comp_status.get("period")
    clock = (comp_status.get("displayClock") or "").strip()
    if state == "in" and period:
        if int(period) > 3:
            label = "OT" if int(period) == 4 else "SO"
        else:
            label = f"P{int(period)}"
        if clock and clock not in ("0.0", "0:00", "0"):
            label = f"{label} {clock}"
        return label
    short = (comp_status.get("type") or {}).get("shortDetail") or ""
    if short and short.lower() not in ("scheduled", "pre-game"):
        return short
    return None


def _record(competitor: dict[str, Any]) -> str | None:
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


def _game_type(season: dict[str, Any]) -> str:
    kind = season.get("type")
    if kind == 1:
        return "preseason"
    if kind == 3:
        return "playoffs"
    return "regular"


def live_game_record(event: dict[str, Any]) -> dict[str, Any]:
    competition = (event.get("competitions") or [{}])[0]
    competitors = competition.get("competitors") or []
    home = next((c for c in competitors if c.get("homeAway") == "home"), {})
    away = next((c for c in competitors if c.get("homeAway") == "away"), {})
    home_team = home.get("team") or {}
    away_team = away.get("team") or {}
    status = competition.get("status") or {}
    season = event.get("season") or {}
    odds = parse_espn_nhl_odds(competition)
    game_type = _game_type(season)
    home_id = home_team.get("id")
    away_id = away_team.get("id")
    return {
        "sport": "nhl",
        "game_id": str(event.get("id")),
        "home_team": home_team.get("displayName") or home_team.get("name") or "Home",
        "away_team": away_team.get("displayName") or away_team.get("name") or "Away",
        "home_team_id": str(home_id) if home_id is not None else "",
        "away_team_id": str(away_id) if away_id is not None else "",
        "home_team_abbr": canonical_abbr(home_team.get("abbreviation")),
        "away_team_abbr": canonical_abbr(away_team.get("abbreviation")),
        "home_logo_url": home_team.get("logo"),
        "away_logo_url": away_team.get("logo"),
        "home_record": _record(home),
        "away_record": _record(away),
        "start_time_utc": event.get("date") or competition.get("date"),
        "status": _status(status),
        "detailed_status": (status.get("type") or {}).get("description", ""),
        "period_label": _period_label(status),
        "home_score": _parse_score(home.get("score")),
        "away_score": _parse_score(away.get("score")),
        "season": season.get("year"),
        "neutral_site": 1 if competition.get("neutralSite") else 0,
        "game_type": game_type,
        "is_preseason": int(game_type == "preseason"),
        "espn_home_ml": odds.get("espn_home_ml"),
        "espn_away_ml": odds.get("espn_away_ml"),
        "espn_spread": odds.get("espn_spread"),
        "espn_ou": odds.get("espn_ou"),
        "home_ml": odds.get("espn_home_ml"),
        "away_ml": odds.get("espn_away_ml"),
        "home_spread_point": odds.get("espn_spread"),
        "ou_line": odds.get("espn_ou"),
    }


def clear_scores_cache() -> None:
    global _scores_cache, _scores_cache_key, _scores_cache_at
    _scores_cache = None
    _scores_cache_key = None
    _scores_cache_at = None


def get_nhl_scores_today(
    game_date: date | None = None,
    *,
    auto_resolve: bool = False,
    force_live: bool = False,
) -> dict[str, Any]:
    from app.services.schedule_nhl import get_nhl_schedule

    requested_date = game_date or slate_today()
    cache_key = f"nhl:{requested_date.isoformat()}:ar={int(auto_resolve)}"
    now = datetime.now(timezone.utc)

    global _scores_cache, _scores_cache_key, _scores_cache_at
    if (
        _scores_cache is not None
        and _scores_cache_key == cache_key
        and _scores_cache_at is not None
        and (now - _scores_cache_at).total_seconds() < SCORES_CACHE_TTL_SECONDS
        and not force_live
    ):
        return {**_scores_cache, "cache_hit": True}

    schedule = get_nhl_schedule(
        game_date,
        auto_resolve=auto_resolve,
        force_live=force_live,
    )
    payload: dict[str, Any] = {
        **schedule,
        "cache_hit": schedule.get("source") == "cache",
        "cache_ttl_seconds": SCORES_CACHE_TTL_SECONDS,
    }
    _scores_cache = payload
    _scores_cache_key = cache_key
    _scores_cache_at = now
    return payload
