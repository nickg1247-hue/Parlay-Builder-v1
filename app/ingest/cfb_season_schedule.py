"""Full-season CFB schedule (completed + remaining) from CFBD.

Used by futures — not the completed-only training parquet.
One GET /games per season. Cache: data/processed/cfb_season_schedule_{year}.json
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.config import PROJECT_ROOT
from app.ingest.cfb import (
    CFBD_BASE_URL,
    REQUEST_RETRIES,
    _is_fbs_relevant_game,
    _parse_game_date,
    _require_api_key,
)
from app.odds.cfb_team_aliases import normalize_team_name

logger = logging.getLogger(__name__)

SCHEDULE_CACHE_DIR = PROJECT_ROOT / "data" / "processed"


def season_schedule_path(season: int) -> Path:
    return SCHEDULE_CACHE_DIR / f"cfb_season_schedule_{int(season)}.json"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_schedule_row(row: dict[str, Any], season: int) -> dict[str, Any] | None:
    """Parse completed or unplayed FBS-relevant CFBD game rows."""
    if not _is_fbs_relevant_game(row):
        return None
    home_team = normalize_team_name(str(row.get("homeTeam") or ""))
    away_team = normalize_team_name(str(row.get("awayTeam") or ""))
    if not home_team or not away_team:
        return None
    game_id = str(row.get("id") or "")
    if not game_id:
        return None
    game_date = _parse_game_date(str(row.get("startDate") or ""))
    if not game_date:
        return None
    week_raw = row.get("week")
    try:
        week = int(week_raw) if week_raw is not None else 0
    except (TypeError, ValueError):
        week = 0
    home_pts = row.get("homePoints")
    away_pts = row.get("awayPoints")
    completed = bool(row.get("completed")) and home_pts is not None and away_pts is not None
    home_score = int(home_pts) if completed else None
    away_score = int(away_pts) if completed else None
    home_win = None
    if completed and home_score != away_score:
        home_win = int(home_score > away_score)
    elif completed:
        completed = False
        home_score = None
        away_score = None
    return {
        "game_id": game_id,
        "date": game_date,
        "season": int(season),
        "week": week,
        "home_team": home_team,
        "away_team": away_team,
        "home_conference": str(row.get("homeConference") or "").strip(),
        "away_conference": str(row.get("awayConference") or "").strip(),
        "conference_game": 1 if row.get("conferenceGame") else 0,
        "neutral_site": 1 if row.get("neutralSite") else 0,
        "completed": completed,
        "home_score": home_score,
        "away_score": away_score,
        "home_win": home_win,
    }


def fetch_season_schedule_rows(season: int, *, api_key: str | None = None) -> list[dict[str, Any]]:
    key = api_key or _require_api_key()
    headers = {"Authorization": f"Bearer {key}"}
    params = {
        "year": str(int(season)),
        "seasonType": "regular",
        "division": "fbs",
    }
    last_error: Exception | None = None
    with httpx.Client(timeout=60.0) as client:
        for attempt in range(REQUEST_RETRIES):
            try:
                response = client.get(
                    f"{CFBD_BASE_URL}/games",
                    params=params,
                    headers=headers,
                )
                if response.status_code == 401:
                    raise SystemExit(
                        "CFBD API returned 401 Unauthorized. Check CFBD_API_KEY in .env."
                    )
                response.raise_for_status()
                data = response.json()
                raw = list(data) if isinstance(data, list) else []
                break
            except SystemExit:
                raise
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "CFB season schedule fetch failed (season %s attempt %s/%s): %s",
                    season,
                    attempt + 1,
                    REQUEST_RETRIES,
                    exc,
                )
        else:
            raise RuntimeError(
                f"Could not fetch CFBD season schedule for {season}"
            ) from last_error

    games: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in raw:
        parsed = parse_schedule_row(row, season)
        if parsed is None or parsed["game_id"] in seen:
            continue
        seen.add(parsed["game_id"])
        games.append(parsed)
    games.sort(key=lambda g: (g["date"], g["game_id"]))
    return games


def load_season_schedule(season: int) -> list[dict[str, Any]]:
    path = season_schedule_path(season)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    rows = payload.get("games") if isinstance(payload, dict) else payload
    return list(rows) if isinstance(rows, list) else []


def ensure_season_schedule(
    season: int,
    *,
    force: bool = False,
    api_key: str | None = None,
) -> list[dict[str, Any]]:
    path = season_schedule_path(season)
    if path.exists() and not force:
        cached = load_season_schedule(season)
        if cached:
            return cached
    games = fetch_season_schedule_rows(season, api_key=api_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "season": int(season),
                "fetched_at": _iso_now(),
                "games_count": len(games),
                "games": games,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.info("Cached %s CFB season schedule games for %s", len(games), season)
    return games
