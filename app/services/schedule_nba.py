"""NBA schedule cache for Phase D (ESPN scoreboard)."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT
from app.services.scores_nba import fetch_nba_scores_day, live_game_record

logger = logging.getLogger(__name__)

PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
SCHEDULE_CACHE_TTL_SECONDS = 6 * 3600


def schedule_cache_path(game_date: date) -> Path:
    return PROCESSED_DIR / f"nba_schedule_{game_date.isoformat()}.json"


def _cache_mtime_utc(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def cache_is_fresh(path: Path, ttl_seconds: int = SCHEDULE_CACHE_TTL_SECONDS) -> bool:
    if not path.exists():
        return False
    age = datetime.now(timezone.utc) - _cache_mtime_utc(path)
    return age.total_seconds() < ttl_seconds


def refresh_schedule_cache(game_date: date | None = None) -> dict[str, Any]:
    """Fetch NBA scoreboard and write ``nba_schedule_{date}.json``."""
    game_date = game_date or date.today()
    events = fetch_nba_scores_day(game_date)
    games = [live_game_record(e) for e in events]
    cached_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "date": game_date.isoformat(),
        "sport": "nba",
        "games": games,
        "games_count": len(games),
        "cached_at": cached_at,
    }
    path = schedule_cache_path(game_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("Wrote NBA schedule cache: %s (%d games)", path.name, len(games))
    return payload


def _load_cache_payload(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def get_nba_schedule(game_date: date | None = None) -> dict[str, Any]:
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


def get_nba_game(game_id: str, game_date: date | None = None) -> dict[str, Any] | None:
    game_date = game_date or date.today()
    schedule = get_nba_schedule(game_date)
    game = next(
        (g for g in schedule.get("games", []) if str(g.get("game_id")) == str(game_id)),
        None,
    )
    if game is None:
        return None
    return {
        "date": schedule.get("date", game_date.isoformat()),
        "source": schedule.get("source"),
        "sport": "nba",
        "game": game,
    }
