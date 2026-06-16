"""Freshness checks for MLB prediction inputs (history, pitcher log, odds)."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from app.config import PROJECT_ROOT
from app.data.pitcher_form import PITCHER_GAME_LOG_PATH, get_pitcher_game_log
from app.models.mlb_baseline import load_games

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
