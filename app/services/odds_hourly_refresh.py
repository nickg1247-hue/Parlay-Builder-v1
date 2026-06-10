"""Hourly refresh of today's odds repository snapshot."""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT
from app.odds.live_odds import live_odds_enabled
from app.odds.odds_repository import (
    get_mlb_odds_for_date,
    last_fetch_meta,
    min_refresh_seconds,
    repository_age_seconds,
)
from app.services.schedule_mlb import get_mlb_schedule

logger = logging.getLogger(__name__)

LAST_HOURLY_ODDS_REFRESH = (
    PROJECT_ROOT / "data" / "processed" / "last_odds_hourly_refresh.json"
)


def hourly_refresh_enabled() -> bool:
    flag = os.getenv("ODDS_HOURLY_REFRESH", "false").strip().lower()
    return flag in ("1", "true", "yes", "on")


def _write_hourly_status(
    *,
    ok: bool,
    game_date: date,
    skipped: str | None = None,
    error: str | None = None,
    odds_source: str | None = None,
) -> None:
    payload: dict[str, Any] = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "ok": ok,
        "date": game_date.isoformat(),
        "skipped": skipped,
        "error": error,
        "odds_source": odds_source,
    }
    LAST_HOURLY_ODDS_REFRESH.parent.mkdir(parents=True, exist_ok=True)
    LAST_HOURLY_ODDS_REFRESH.write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )


def load_hourly_refresh_status() -> dict[str, Any] | None:
    if not LAST_HOURLY_ODDS_REFRESH.exists():
        return None
    try:
        return json.loads(LAST_HOURLY_ODDS_REFRESH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read %s: %s", LAST_HOURLY_ODDS_REFRESH, exc)
        return None


def run_hourly_odds_refresh(game_date: date | None = None) -> int:
    """
    Refresh today's repository from Odds API (quota-gated).

    Returns 0 on success or benign skip; 1 only on unexpected failure.
    """
    game_date = game_date or date.today()

    if not live_odds_enabled():
        logger.info("Hourly odds refresh skipped: USE_LIVE_ODDS=false")
        _write_hourly_status(
            ok=True,
            game_date=game_date,
            skipped="USE_LIVE_ODDS=false",
        )
        return 0

    schedule = get_mlb_schedule(game_date)
    games = schedule.get("games") or []
    if not games:
        logger.info("Hourly odds refresh skipped: no games on %s", game_date.isoformat())
        _write_hourly_status(
            ok=True,
            game_date=game_date,
            skipped="no_games_on_slate",
        )
        return 0

    age = repository_age_seconds(game_date)
    hourly_min = max(min_refresh_seconds(), 3300)
    if age is not None and age < hourly_min:
        logger.info(
            "Hourly odds refresh skipped: last fetch %.0fs ago (min %ss)",
            age,
            hourly_min,
        )
        _write_hourly_status(
            ok=True,
            game_date=game_date,
            skipped=f"recent_fetch_{int(age)}s",
        )
        return 0

    try:
        get_mlb_odds_for_date(game_date, force_refresh=True)
        meta = last_fetch_meta()
        if meta.get("quota_denied"):
            reason = meta.get("denied_reason", "quota")
            logger.warning("Hourly odds refresh skipped: %s", reason)
            _write_hourly_status(
                ok=True,
                game_date=game_date,
                skipped=reason,
            )
            return 0

        logger.info("Hourly odds refresh OK for %s", game_date.isoformat())
        _write_hourly_status(
            ok=True,
            game_date=game_date,
            odds_source=meta.get("source"),
        )
        return 0
    except Exception as exc:
        logger.error("Hourly odds refresh failed: %s", exc, exc_info=True)
        _write_hourly_status(ok=False, game_date=game_date, error=str(exc))
        return 1
