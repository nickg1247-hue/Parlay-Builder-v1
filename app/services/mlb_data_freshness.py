"""Freshness checks for MLB prediction inputs (history, pitcher log, odds)."""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from app.config import PROJECT_ROOT
from app.data.pitcher_form import PITCHER_GAME_LOG_PATH, get_pitcher_game_log
from app.models.mlb_baseline import load_games
from app.odds.live_odds import live_odds_enabled

logger = logging.getLogger(__name__)

MAX_HISTORY_GAP_DAYS = 2
MAX_PITCHER_LOG_AGE_DAYS = 7
MAX_ODDS_AGE_HOURS = 48


def _file_age_days(path: Path) -> float | None:
    if not path.exists():
        return None
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return (datetime.now(timezone.utc) - mtime).total_seconds() / 86400.0


def check_mlb_prediction_freshness(
    board_date: date,
    *,
    use_cache: bool = False,
) -> dict[str, Any]:
    """Return stale flags and whether strong model picks should be suppressed."""
    issues: list[str] = []
    block_strong_picks = False

    if use_cache:
        return {
            "stale": False,
            "issues": [],
            "block_strong_picks": False,
            "history_max_date": None,
            "history_gap_days": None,
        }

    try:
        hist = load_games()
        max_dt = pd.to_datetime(hist["date"]).max()
        history_max = max_dt.date() if pd.notna(max_dt) else None
    except Exception:
        history_max = None
        issues.append("Game history unavailable — run ingest before live picks.")
        block_strong_picks = True

    history_gap = None
    if history_max is not None:
        history_gap = (board_date - history_max).days
        if history_gap > MAX_HISTORY_GAP_DAYS:
            issues.append(
                f"Game history last updated {history_max.isoformat()} "
                f"({history_gap} days before board date). Run scripts/ingest_mlb.py."
            )
            block_strong_picks = True

    log = get_pitcher_game_log()
    if log.empty:
        issues.append("Pitcher game log empty — bullpen/L3 features use fallbacks.")
        block_strong_picks = True
    else:
        log_age = _file_age_days(PITCHER_GAME_LOG_PATH)
        if log_age is not None and log_age > MAX_PITCHER_LOG_AGE_DAYS:
            issues.append(
                f"Pitcher log file is {log_age:.0f} days old — re-run ingest for fresh bullpen/L3."
            )
            block_strong_picks = True

    odds_path = PROJECT_ROOT / "data" / "processed" / "odds_repository" / f"{board_date.isoformat()}.json"
    if odds_path.exists():
        try:
            payload = json.loads(odds_path.read_text(encoding="utf-8"))
            fetched = payload.get("fetched_at")
            if fetched:
                ft = datetime.fromisoformat(str(fetched).replace("Z", "+00:00"))
                age_h = (datetime.now(timezone.utc) - ft).total_seconds() / 3600.0
                if age_h > MAX_ODDS_AGE_HOURS:
                    issues.append(
                        f"Odds snapshot for {board_date} is {age_h:.0f}h old — refresh live odds."
                    )
        except (json.JSONDecodeError, TypeError, ValueError):
            issues.append("Odds repository snapshot unreadable for board date.")
    elif not use_cache:
        issues.append(
            f"No odds repository snapshot for {board_date.isoformat()} — market columns use fallbacks."
        )

    return {
        "stale": bool(issues),
        "issues": issues,
        "block_strong_picks": block_strong_picks,
        "history_max_date": history_max.isoformat() if history_max else None,
        "history_gap_days": history_gap,
    }


def _auto_ingest_enabled() -> bool:
    raw = os.getenv("MLB_AUTO_INGEST", "true").strip().lower()
    return raw not in ("0", "false", "no")


def _history_gap_days(board_date: date) -> int | None:
    try:
        hist = load_games()
        if hist.empty:
            return None
        max_dt = pd.to_datetime(hist["date"]).max()
        if pd.isna(max_dt):
            return None
        return int((board_date - max_dt.date()).days)
    except Exception:
        return None


def ensure_mlb_ingest_fresh(board_date: date, *, use_cache: bool = False) -> dict[str, Any]:
    """
    Run MLB ingest when game history or pitcher log is too old for live picks.
    No-op for demo/cache boards or when MLB_AUTO_INGEST=false.
    """
    if use_cache or not _auto_ingest_enabled():
        return {"ran": False, "reason": "skipped"}

    gap = _history_gap_days(board_date)
    log = get_pitcher_game_log()
    log_age = _file_age_days(PITCHER_GAME_LOG_PATH)

    needs_history = gap is None or gap > MAX_HISTORY_GAP_DAYS
    needs_pitcher = log.empty or (
        log_age is not None and log_age > MAX_PITCHER_LOG_AGE_DAYS
    )
    if not needs_history and not needs_pitcher:
        return {"ran": False, "reason": "fresh", "history_gap_days": gap}

    reasons: list[str] = []
    if needs_history:
        if gap is None:
            reasons.append("game history missing")
        else:
            reasons.append(f"game history {gap}d behind board date")
    if needs_pitcher:
        if log.empty:
            reasons.append("pitcher log empty")
        elif log_age is not None:
            reasons.append(f"pitcher log {log_age:.0f}d old")

    logger.info(
        "Auto MLB ingest (%s) before board %s",
        ", ".join(reasons),
        board_date.isoformat(),
    )
    try:
        from app.ingest.mlb import run_ingest

        run_ingest()
    except Exception as exc:
        logger.exception("Auto MLB ingest failed")
        return {
            "ran": False,
            "reason": "error",
            "error": str(exc),
            "message": f"MLB ingest failed ({exc}) — picks may use stale team form.",
        }

    new_gap = _history_gap_days(board_date)
    return {
        "ran": True,
        "reason": "refreshed",
        "history_gap_days": new_gap,
        "message": (
            "Refreshed MLB game history and pitcher log for current-season features."
        ),
    }


def _odds_snapshot_age_hours(board_date: date) -> float | None:
    odds_path = (
        PROJECT_ROOT / "data" / "processed" / "odds_repository" / f"{board_date.isoformat()}.json"
    )
    if not odds_path.exists():
        return None
    try:
        payload = json.loads(odds_path.read_text(encoding="utf-8"))
        fetched = payload.get("fetched_at")
        if not fetched:
            return None
        ft = datetime.fromisoformat(str(fetched).replace("Z", "+00:00"))
        if ft.tzinfo is None:
            ft = ft.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ft).total_seconds() / 3600.0
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def ensure_odds_snapshot(
    board_date: date,
    *,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """
    Fetch today's odds repository snapshot when live odds are enabled and
    the on-disk file is missing or older than MAX_ODDS_AGE_HOURS.
    """
    if not live_odds_enabled():
        return {
            "ran": False,
            "reason": "live_odds_disabled",
            "message": None,
        }

    from app.odds.odds_repository import get_mlb_odds_for_date, has_date

    age_h = _odds_snapshot_age_hours(board_date)
    needs = force_refresh or not has_date(board_date)
    if not needs and age_h is not None and age_h > MAX_ODDS_AGE_HOURS:
        needs = True
    if not needs:
        return {"ran": False, "reason": "fresh", "age_hours": age_h, "message": None}

    logger.info(
        "Auto odds fetch for %s (force=%s, age_h=%s)",
        board_date.isoformat(),
        force_refresh,
        age_h,
    )
    games, source = get_mlb_odds_for_date(
        board_date,
        force_refresh=True,
        include_totals=True,
        include_spreads=True,
    )
    if games:
        return {
            "ran": True,
            "reason": "fetched",
            "source": source,
            "games": len(games),
            "message": f"Fetched live sportsbook lines ({source}, {len(games)} games).",
        }
    return {
        "ran": False,
        "reason": "empty",
        "message": (
            "Live odds enabled but no lines returned — check ODDS_API_KEY quota "
            "or try again later."
        ),
    }
