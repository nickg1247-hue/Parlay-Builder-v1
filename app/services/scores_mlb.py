"""Live MLB scores for ticker and slate polling (Phase B)."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

import httpx

from app.odds.team_aliases import normalize_team_name
from app.services.schedule_mlb import _update_teams_map, team_logo_url

logger = logging.getLogger(__name__)

MLB_SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
SCORES_CACHE_TTL_SECONDS = 45

_scores_cache: dict[str, Any] | None = None
_scores_cache_key: str | None = None
_scores_cache_at: datetime | None = None


def _ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def period_label(game: dict[str, Any]) -> str | None:
    """e.g. Bot 7th from MLB linescore hydrate."""
    linescore = game.get("linescore") or {}
    inning = linescore.get("currentInning")
    state = (linescore.get("inningState") or "").strip()
    if not inning:
        return None
    inning_n = int(inning)
    inning_text = f"{inning_n}{_ordinal(inning_n)}"
    state_lower = state.lower()
    if state_lower.startswith("top"):
        return f"Top {inning_text}"
    if state_lower.startswith("bot"):
        return f"Bot {inning_text}"
    if state_lower == "middle":
        return f"Mid {inning_text}"
    if state_lower == "end":
        return "End"
    return inning_text


def fetch_mlb_scores_day(game_date: date) -> list[dict[str, Any]]:
    """Fetch today's games with linescore (live inning + scores)."""
    params = {
        "sportId": 1,
        "date": game_date.isoformat(),
        "hydrate": "linescore",
    }
    with httpx.Client(timeout=30.0) as client:
        response = client.get(MLB_SCHEDULE_URL, params=params)
        response.raise_for_status()
        data = response.json()
    games: list[dict[str, Any]] = []
    for day in data.get("dates", []):
        games.extend(day.get("games", []))
    return games


def live_game_record(game: dict[str, Any]) -> dict[str, Any]:
    teams = game.get("teams") or {}
    home = teams.get("home") or {}
    away = teams.get("away") or {}
    status = game.get("status") or {}
    home_team = home.get("team") or {}
    away_team = away.get("team") or {}
    home_id = int(home_team.get("id") or 0)
    away_id = int(away_team.get("id") or 0)
    abstract = status.get("abstractGameState") or ""
    return {
        "sport": "mlb",
        "game_id": str(game.get("gamePk") or ""),
        "home_team": normalize_team_name(home_team.get("name") or "Home"),
        "away_team": normalize_team_name(away_team.get("name") or "Away"),
        "home_team_id": home_id,
        "away_team_id": away_id,
        "home_logo_url": team_logo_url(home_id) if home_id else None,
        "away_logo_url": team_logo_url(away_id) if away_id else None,
        "start_time_utc": game.get("gameDate"),
        "status": abstract or status.get("detailedState", ""),
        "detailed_status": status.get("detailedState", ""),
        "period_label": period_label(game),
        "home_score": home.get("score"),
        "away_score": away.get("score"),
    }


def clear_scores_cache() -> None:
    """Reset in-memory cache (for tests)."""
    global _scores_cache, _scores_cache_key, _scores_cache_at
    _scores_cache = None
    _scores_cache_key = None
    _scores_cache_at = None


def get_scores_today(sport: str = "mlb", game_date: date | None = None) -> dict[str, Any]:
    """Today's scores with short TTL cache (separate from 6h schedule cache)."""
    if sport != "mlb":
        raise ValueError(f"Unsupported sport: {sport}")

    game_date = game_date or date.today()
    cache_key = f"{sport}:{game_date.isoformat()}"
    now = datetime.now(timezone.utc)

    global _scores_cache, _scores_cache_key, _scores_cache_at
    if (
        _scores_cache is not None
        and _scores_cache_key == cache_key
        and _scores_cache_at is not None
        and (now - _scores_cache_at).total_seconds() < SCORES_CACHE_TTL_SECONDS
    ):
        return {**_scores_cache, "cache_hit": True}

    try:
        api_games = fetch_mlb_scores_day(game_date)
        games = [live_game_record(g) for g in api_games]
    except Exception as exc:
        logger.warning("MLB scores fetch failed for %s: %s", game_date, exc)
        if _scores_cache is not None and _scores_cache_key == cache_key:
            stale = {**_scores_cache, "cache_hit": True, "stale": True}
            stale["error"] = "Live scores temporarily unavailable — showing last update."
            return stale
        return {
            "sport": sport,
            "date": game_date.isoformat(),
            "games": [],
            "games_count": 0,
            "cached_at": now.isoformat(),
            "cache_ttl_seconds": SCORES_CACHE_TTL_SECONDS,
            "source": "live",
            "cache_hit": False,
            "error": "Live scores temporarily unavailable.",
        }
    if games:
        _update_teams_map(games)

    payload: dict[str, Any] = {
        "sport": sport,
        "date": game_date.isoformat(),
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
    logger.debug("Live scores refreshed: %s (%d games)", game_date, len(games))
    return payload


def get_live_game(game_id: str, game_date: date | None = None) -> dict[str, Any] | None:
    """Single game from live scores feed."""
    scores = get_scores_today(game_date=game_date)
    return next(
        (g for g in scores.get("games", []) if str(g.get("game_id")) == str(game_id)),
        None,
    )
