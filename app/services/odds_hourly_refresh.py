"""Hourly refresh of today's odds repository snapshot."""

from __future__ import annotations

import logging
import os
from datetime import date

from app.odds.live_odds import live_odds_enabled
from app.odds.odds_repository import get_mlb_odds_for_date, last_fetch_meta
from app.services.schedule_mlb import get_mlb_schedule

logger = logging.getLogger(__name__)


def hourly_refresh_enabled() -> bool:
    flag = os.getenv("ODDS_HOURLY_REFRESH", "false").strip().lower()
    return flag in ("1", "true", "yes", "on")


def run_hourly_odds_refresh(game_date: date | None = None) -> int:
    """
    Refresh today's repository from Odds API (quota-gated).

    Returns 0 on success or benign skip; 1 only on unexpected failure.
    """
    if not live_odds_enabled():
        logger.info("Hourly odds refresh skipped: USE_LIVE_ODDS=false")
        return 0

    game_date = game_date or date.today()
    schedule = get_mlb_schedule(game_date)
    games = schedule.get("games") or []
    if not games:
        logger.info("Hourly odds refresh skipped: no games on %s", game_date.isoformat())
        return 0

    try:
        get_mlb_odds_for_date(game_date, force_refresh=True)
        meta = last_fetch_meta()
        if meta.get("quota_denied"):
            logger.warning(
                "Hourly odds refresh skipped: %s",
                meta.get("denied_reason", "quota"),
            )
            return 0

        logger.info("Hourly odds refresh OK for %s", game_date.isoformat())
        return 0
    except Exception as exc:
        logger.error("Hourly odds refresh failed: %s", exc, exc_info=True)
        return 1
