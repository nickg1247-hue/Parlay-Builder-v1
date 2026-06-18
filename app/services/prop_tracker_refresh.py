"""Scheduled backfill for the MLB prop pick tracker."""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT
from app.services.prop_pick_tracker import backfill_prop_results, summarize_prop_tracker

logger = logging.getLogger(__name__)

LAST_PROP_TRACKER_REFRESH = (
    PROJECT_ROOT / "data" / "processed" / "last_prop_tracker_refresh.json"
)


def prop_tracker_auto_enabled() -> bool:
    raw = os.getenv("PROP_TRACKER_AUTO", "true").strip().lower()
    return raw in ("1", "true", "yes", "on")


def min_refresh_seconds() -> int:
    return max(300, int(os.getenv("PROP_TRACKER_MIN_SECONDS", "3600")))


def load_prop_tracker_refresh_status() -> dict[str, Any] | None:
    if not LAST_PROP_TRACKER_REFRESH.exists():
        return None
    try:
        return json.loads(LAST_PROP_TRACKER_REFRESH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read %s: %s", LAST_PROP_TRACKER_REFRESH, exc)
        return None


def _seconds_since_last_run() -> float | None:
    status = load_prop_tracker_refresh_status()
    if not status or not status.get("ran_at"):
        return None
    try:
        ran_at = datetime.fromisoformat(str(status["ran_at"]).replace("Z", "+00:00"))
        if ran_at.tzinfo is None:
            ran_at = ran_at.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ran_at).total_seconds()
    except (TypeError, ValueError):
        return None


def _write_status(
    *,
    ok: bool,
    game_date: date,
    skipped: str | None = None,
    error: str | None = None,
    backfill: dict[str, Any] | None = None,
    summary: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "ok": ok,
        "date": game_date.isoformat(),
        "skipped": skipped,
        "error": error,
        "backfill": backfill,
        "props_settled": (summary or {}).get("props_settled"),
        "overall_hit_rate": (summary or {}).get("overall_hit_rate"),
    }
    LAST_PROP_TRACKER_REFRESH.parent.mkdir(parents=True, exist_ok=True)
    LAST_PROP_TRACKER_REFRESH.write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )


def run_prop_tracker_refresh(game_date: date | None = None) -> int:
    """
    Grade pending offered props against final box-score stats.

    Returns 0 on success or benign skip; 1 on unexpected failure.
    """
    game_date = game_date or date.today()

    if not prop_tracker_auto_enabled():
        logger.info("Prop tracker refresh skipped: PROP_TRACKER_AUTO=false")
        _write_status(ok=True, game_date=game_date, skipped="PROP_TRACKER_AUTO=false")
        return 0

    age = _seconds_since_last_run()
    min_secs = min_refresh_seconds()
    if age is not None and age < min_secs:
        logger.info(
            "Prop tracker refresh skipped: last run %.0fs ago (min %ss)",
            age,
            min_secs,
        )
        _write_status(
            ok=True,
            game_date=game_date,
            skipped=f"recent_run_{int(age)}s",
        )
        return 0

    try:
        backfill = backfill_prop_results(None)
        summary = summarize_prop_tracker(days=30)
        logger.info(
            "Prop tracker refresh OK: updated=%s pending=%s settled=%s hit_rate=%s",
            backfill.get("updated"),
            backfill.get("pending"),
            summary.get("props_settled"),
            summary.get("overall_hit_rate"),
        )
        _write_status(
            ok=True,
            game_date=game_date,
            backfill=backfill,
            summary=summary,
        )
        return 0
    except Exception as exc:
        logger.error("Prop tracker refresh failed: %s", exc, exc_info=True)
        _write_status(ok=False, game_date=game_date, error=str(exc))
        return 1
