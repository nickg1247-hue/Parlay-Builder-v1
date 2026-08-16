"""Full-season NFL regular-season schedule (completed + remaining) from ESPN.

Used by futures — not the completed-only training parquet.
One ESPN week scoreboard request per week (1–18).
Cache: data/processed/nfl_season_schedule_{year}.json
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.config import PROJECT_ROOT
from app.ingest.nfl import (
    MAX_WEEK,
    REGULAR_SEASON_TYPE,
    REQUEST_SLEEP_SECONDS,
    _fetch_week,
    parse_espn_schedule_event,
)

logger = logging.getLogger(__name__)

SCHEDULE_CACHE_DIR = PROJECT_ROOT / "data" / "processed"


def season_schedule_path(season: int) -> Path:
    return SCHEDULE_CACHE_DIR / f"nfl_season_schedule_{int(season)}.json"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_regular_season_schedule(
    season: int,
    *,
    client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    """Pull regular-season weeks 1–18, including games that have not been played."""
    games: list[dict[str, Any]] = []
    own_client = client is None
    http = client or httpx.Client(timeout=60.0)
    try:
        for week in range(1, MAX_WEEK + 1):
            events = _fetch_week(http, season, week, REGULAR_SEASON_TYPE)
            for event in events:
                parsed = parse_espn_schedule_event(event, season=season, week=week)
                if parsed is None:
                    continue
                games.append(parsed)
            time.sleep(REQUEST_SLEEP_SECONDS)
    finally:
        if own_client:
            http.close()

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for game in games:
        gid = str(game["game_id"])
        if gid in seen:
            continue
        seen.add(gid)
        unique.append(game)
    unique.sort(key=lambda g: (g["date"], g["game_id"]))
    logger.info(
        "NFL %s regular-season schedule: %s games (%s completed)",
        season,
        len(unique),
        sum(1 for g in unique if g.get("completed")),
    )
    return unique


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
    client: httpx.Client | None = None,
) -> list[dict[str, Any]]:
    path = season_schedule_path(season)
    if path.exists() and not force:
        cached = load_season_schedule(season)
        if cached:
            return cached
    games = fetch_regular_season_schedule(season, client=client)
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
    logger.info("Cached %s NFL regular-season schedule games for %s", len(games), season)
    return games
