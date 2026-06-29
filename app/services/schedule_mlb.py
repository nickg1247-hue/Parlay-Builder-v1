"""MLB schedule cache for morning refresh and Phase A/B UI."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT
from app.odds.team_aliases import normalize_team_name
from app.parlay.slate import fetch_mlb_schedule_day, filter_board_games

logger = logging.getLogger(__name__)

PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
MLB_TEAMS_PATH = PROCESSED_DIR / "mlb_teams.json"
DAILY_BOARD_CACHE = PROCESSED_DIR / "daily_board.json"
SCHEDULE_CACHE_TTL_SECONDS = 6 * 3600
LOGO_URL_TEMPLATE = (
    "https://www.mlbstatic.com/team-logos/team-cap-on-dark/{team_id}.svg"
)


def schedule_cache_path(game_date: date) -> Path:
    return PROCESSED_DIR / f"mlb_schedule_{game_date.isoformat()}.json"


def team_logo_url(team_id: int) -> str:
    return LOGO_URL_TEMPLATE.format(team_id=team_id)


def _game_record(game: dict[str, Any]) -> dict[str, Any]:
    home = game["teams"]["home"]
    away = game["teams"]["away"]
    status = game.get("status", {})
    home_id = home["team"]["id"]
    away_id = away["team"]["id"]
    return {
        "game_id": str(game["gamePk"]),
        "home_team": normalize_team_name(home["team"]["name"]),
        "away_team": normalize_team_name(away["team"]["name"]),
        "home_team_id": home_id,
        "away_team_id": away_id,
        "home_logo_url": team_logo_url(home_id),
        "away_logo_url": team_logo_url(away_id),
        "start_time_utc": game.get("gameDate"),
        "status": status.get("abstractGameState") or status.get("detailedState", ""),
        "home_score": home.get("score"),
        "away_score": away.get("score"),
    }


def _cache_mtime_utc(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def cache_is_fresh(path: Path, ttl_seconds: int = SCHEDULE_CACHE_TTL_SECONDS) -> bool:
    if not path.exists():
        return False
    age = datetime.now(timezone.utc) - _cache_mtime_utc(path)
    return age.total_seconds() < ttl_seconds


def _update_teams_map(games: list[dict[str, Any]]) -> dict[str, int]:
    teams: dict[str, int] = {}
    if MLB_TEAMS_PATH.exists():
        try:
            teams = {
                k: int(v) for k, v in json.loads(MLB_TEAMS_PATH.read_text(encoding="utf-8")).items()
            }
        except (json.JSONDecodeError, TypeError, ValueError):
            teams = {}
    for game in games:
        teams[game["home_team"]] = int(game["home_team_id"])
        teams[game["away_team"]] = int(game["away_team_id"])
    MLB_TEAMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    MLB_TEAMS_PATH.write_text(
        json.dumps(teams, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return teams


def refresh_schedule_cache(game_date: date | None = None) -> dict[str, Any]:
    """Fetch MLB schedule and write ``mlb_schedule_{date}.json``."""
    game_date = game_date or date.today()
    api_games = filter_board_games(fetch_mlb_schedule_day(game_date), game_date)
    games = [_game_record(g) for g in api_games]
    _update_teams_map(games)
    cached_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "date": game_date.isoformat(),
        "games": games,
        "games_count": len(games),
        "cached_at": cached_at,
    }
    path = schedule_cache_path(game_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("Wrote schedule cache: %s (%d games)", path.name, len(games))
    return payload


def _load_cache_payload(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if "cached_at" not in payload:
        payload["cached_at"] = _cache_mtime_utc(path).isoformat()
    for game in payload.get("games", []):
        if "home_logo_url" not in game and game.get("home_team_id") is not None:
            game["home_logo_url"] = team_logo_url(int(game["home_team_id"]))
        if "away_logo_url" not in game and game.get("away_team_id") is not None:
            game["away_logo_url"] = team_logo_url(int(game["away_team_id"]))
    return payload


def get_mlb_schedule(game_date: date | None = None) -> dict[str, Any]:
    """Return schedule from cache if fresh (<6h), else fetch and write."""
    game_date = game_date or date.today()
    path = schedule_cache_path(game_date)
    if cache_is_fresh(path):
        payload = _load_cache_payload(path)
        payload["source"] = "cache"
        return payload
    payload = refresh_schedule_cache(game_date)
    payload["source"] = "api"
    return payload


def _daily_board_row(game_id: str) -> dict[str, Any] | None:
    if not DAILY_BOARD_CACHE.exists():
        return None
    try:
        board = json.loads(DAILY_BOARD_CACHE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    for row in board.get("slate", []):
        if str(row.get("game_id")) == str(game_id):
            return row
    return None


def _daily_board_date() -> date | None:
    if not DAILY_BOARD_CACHE.exists():
        return None
    try:
        board = json.loads(DAILY_BOARD_CACHE.read_text(encoding="utf-8"))
        raw = board.get("date")
        return date.fromisoformat(str(raw)) if raw else None
    except (json.JSONDecodeError, OSError, ValueError):
        return None


def _team_ids_for_names(home_team: str, away_team: str) -> tuple[int | None, int | None]:
    if not MLB_TEAMS_PATH.exists():
        return None, None
    try:
        teams = json.loads(MLB_TEAMS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None, None
    home_id = teams.get(home_team)
    away_id = teams.get(away_team)
    return (
        int(home_id) if home_id is not None else None,
        int(away_id) if away_id is not None else None,
    )


def _game_from_board_row(row: dict[str, Any]) -> dict[str, Any]:
    home_team = str(row.get("home_team") or "")
    away_team = str(row.get("away_team") or "")
    home_id, away_id = _team_ids_for_names(home_team, away_team)
    return {
        "game_id": str(row.get("game_id") or ""),
        "home_team": home_team,
        "away_team": away_team,
        "home_team_id": home_id,
        "away_team_id": away_id,
        "home_logo_url": team_logo_url(home_id) if home_id else None,
        "away_logo_url": team_logo_url(away_id) if away_id else None,
        "start_time_utc": row.get("start_time_utc"),
        "status": row.get("status") or "Scheduled",
        "home_score": row.get("home_score"),
        "away_score": row.get("away_score"),
    }


def _games_for_date(game_date: date, *, allow_fetch: bool) -> list[dict[str, Any]]:
    path = schedule_cache_path(game_date)
    if path.exists():
        return _load_cache_payload(path).get("games") or []
    if allow_fetch and game_date == date.today():
        return get_mlb_schedule(game_date).get("games") or []
    return []


def _find_game_record(
    game_id: str,
    game_date: date,
) -> tuple[dict[str, Any] | None, date | None]:
    """Locate a game on the requested date, nearby days, or the daily board."""
    gid = str(game_id)
    search_dates = [game_date, game_date - timedelta(days=1), game_date + timedelta(days=1)]
    seen: set[str] = set()
    for idx, search_date in enumerate(search_dates):
        iso = search_date.isoformat()
        if iso in seen:
            continue
        seen.add(iso)
        for game in _games_for_date(search_date, allow_fetch=idx == 0):
            if str(game.get("game_id")) == gid:
                return game, search_date

    row = _daily_board_row(gid)
    if row and row.get("home_team") and row.get("away_team"):
        resolved = _daily_board_date() or game_date
        return _game_from_board_row(row), resolved

    return None, None


def get_mlb_game(game_id: str, game_date: date | None = None) -> dict[str, Any] | None:
    """Single game metadata plus daily-board slate row when available."""
    game_date = game_date or date.today()
    game, resolved_date = _find_game_record(game_id, game_date)
    if game is None or resolved_date is None:
        return None
    schedule_source = "cache"
    if resolved_date == date.today():
        schedule = get_mlb_schedule(resolved_date)
        schedule_source = schedule.get("source", "cache")
    return {
        "date": resolved_date.isoformat(),
        "source": schedule_source,
        "game": game,
        "board_row": _daily_board_row(game_id),
    }
