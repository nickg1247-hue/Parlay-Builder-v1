"""Persistent NBA Summer League odds snapshots (Odds API basketball_nba_summer_league)."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT
from app.odds.live_odds import live_odds_enabled
from app.odds.nba_odds_repository import normalize_nba_events
from app.odds.odds_repository import ApiFetchResult, _release_quota_slot, _try_acquire_quota_slot
from app.odds.the_odds_api import fetch_live_nba_summer_odds

logger = logging.getLogger(__name__)

DEFAULT_REPO_DIR = PROJECT_ROOT / "data" / "processed" / "nba_summer_odds_repository"


def _repo_root() -> Path:
    import os

    override = os.getenv("NBA_SUMMER_ODDS_REPOSITORY_DIR", "").strip()
    return Path(override) if override else DEFAULT_REPO_DIR


def repository_path(game_date: date) -> Path:
    return _repo_root() / f"{game_date.isoformat()}.json"


def load_date(game_date: date) -> dict[str, Any] | None:
    path = repository_path(game_date)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read NBA Summer odds repository %s: %s", path, exc)
        return None


def has_date(game_date: date) -> bool:
    return repository_path(game_date).exists()


def save_date(game_date: date, payload: dict[str, Any]) -> None:
    root = _repo_root()
    root.mkdir(parents=True, exist_ok=True)
    repository_path(game_date).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def fetch_nba_summer_from_api_if_allowed(
    game_date: date,
    *,
    include_spreads: bool = True,
    include_totals: bool = True,
) -> ApiFetchResult:
    if game_date < date.today():
        return ApiFetchResult(denied=True, denied_reason="nba_summer_live_only_no_historical")
    if not live_odds_enabled():
        return ApiFetchResult(denied=True, denied_reason="live_odds_disabled")

    allowed, deny_reason = _try_acquire_quota_slot()
    if not allowed:
        return ApiFetchResult(denied=True, denied_reason=deny_reason)

    try:
        events = fetch_live_nba_summer_odds(
            include_spreads=include_spreads,
            include_totals=include_totals,
        )
        normalized = normalize_nba_events(events or [])
        return ApiFetchResult(events=normalized, source="the_odds_api_live")
    except Exception as exc:
        _release_quota_slot()
        logger.warning(
            "NBA Summer Odds API HTTP failed for %s: %s", game_date.isoformat(), exc
        )
        return ApiFetchResult(error=str(exc))


def get_nba_summer_odds_for_date(
    game_date: date,
    *,
    force_refresh: bool = False,
    include_spreads: bool = True,
    include_totals: bool = True,
) -> tuple[list[dict[str, Any]] | None, str]:
    if has_date(game_date) and not force_refresh:
        payload = load_date(game_date)
        if payload:
            return payload.get("games", []), payload.get("source", "repository")

    if game_date < date.today():
        return None, "none"

    api_result = fetch_nba_summer_from_api_if_allowed(
        game_date,
        include_spreads=include_spreads,
        include_totals=include_totals,
    )
    if api_result.denied or api_result.error or api_result.events is None:
        if has_date(game_date):
            payload = load_date(game_date)
            if payload:
                return payload.get("games", []), payload.get("source", "repository_stale")
        return None, "none"

    payload = {
        "date": game_date.isoformat(),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": api_result.source or "the_odds_api_live",
        "games": api_result.events,
    }
    save_date(game_date, payload)
    return api_result.events, payload["source"]
