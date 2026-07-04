"""Periodic MLB ingest + daily board rebuild while the server is running."""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT
from app.models.constants import DEFAULT_MIN_EDGE
from app.odds.live_odds import live_odds_enabled
from app.parlay.ev_ranker import DEFAULT_MAX_PARLAYS
from app.services.daily_board import board_disk_date_matches, build_daily_board
from app.services.mlb_data_freshness import ensure_mlb_ingest_fresh, ensure_odds_snapshot
from app.services.schedule_mlb import get_mlb_schedule

logger = logging.getLogger(__name__)

LAST_MLB_PERIODIC_REFRESH = (
    PROJECT_ROOT / "data" / "processed" / "last_mlb_periodic_refresh.json"
)

DEFAULT_INTERVAL_SECONDS = 10_800  # 3 hours


def periodic_refresh_enabled() -> bool:
    flag = os.getenv("MLB_PERIODIC_REFRESH", "true").strip().lower()
    return flag not in ("0", "false", "no", "off")


def periodic_refresh_interval_seconds() -> int:
    raw = os.getenv("MLB_PERIODIC_REFRESH_SECONDS", "").strip()
    if raw:
        try:
            return max(3600, int(raw))
        except ValueError:
            pass
    return DEFAULT_INTERVAL_SECONDS


def _force_ingest_each_cycle() -> bool:
    raw = os.getenv("MLB_PERIODIC_FORCE_INGEST", "true").strip().lower()
    return raw not in ("0", "false", "no", "off")


def load_periodic_refresh_status() -> dict[str, Any] | None:
    if not LAST_MLB_PERIODIC_REFRESH.exists():
        return None
    try:
        return json.loads(LAST_MLB_PERIODIC_REFRESH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read %s: %s", LAST_MLB_PERIODIC_REFRESH, exc)
        return None


def _seconds_since_last_run() -> float | None:
    status = load_periodic_refresh_status()
    if not status:
        return None
    ran_at = status.get("ran_at")
    if not ran_at:
        return None
    try:
        ts = datetime.fromisoformat(str(ran_at).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds()
    except (TypeError, ValueError):
        return None


def _write_status(
    *,
    ok: bool,
    game_date: date,
    skipped: str | None = None,
    error: str | None = None,
    ingest_ran: bool = False,
    board_games: int | None = None,
    odds_source: str | None = None,
) -> None:
    payload: dict[str, Any] = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "ok": ok,
        "date": game_date.isoformat(),
        "skipped": skipped,
        "error": error,
        "ingest_ran": ingest_ran,
        "board_games": board_games,
        "odds_source": odds_source,
    }
    LAST_MLB_PERIODIC_REFRESH.parent.mkdir(parents=True, exist_ok=True)
    LAST_MLB_PERIODIC_REFRESH.write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )


def run_mlb_periodic_refresh(game_date: date | None = None) -> int:
    """
    Refresh MLB history (ingest) and rebuild today's daily board.

    Runs on a timer (default every 3h) so game pages pick up current stats
    without manual scripts. Returns 0 on success or benign skip.
    """
    game_date = game_date or date.today()

    if not periodic_refresh_enabled():
        _write_status(ok=True, game_date=game_date, skipped="disabled")
        return 0

    board_stale = not board_disk_date_matches(game_date)
    since = _seconds_since_last_run()
    interval = periodic_refresh_interval_seconds()
    if not board_stale and since is not None and since < interval:
        _write_status(
            ok=True,
            game_date=game_date,
            skipped=f"recent_run_{int(since)}s",
        )
        return 0
    if board_stale:
        logger.warning(
            "MLB periodic refresh: on-disk board date mismatch — forcing rebuild for %s",
            game_date.isoformat(),
        )

    schedule = get_mlb_schedule(game_date)
    if not (schedule.get("games") or []):
        logger.info("MLB periodic refresh skipped: no games on %s", game_date.isoformat())
        _write_status(ok=True, game_date=game_date, skipped="no_games_on_slate")
        return 0

    ingest_ran = False
    try:
        ingest_out = ensure_mlb_ingest_fresh(game_date, use_cache=False)
        ingest_ran = bool(ingest_out.get("ran"))

        if _force_ingest_each_cycle() and not ingest_ran:
            from app.ingest.mlb import run_ingest

            logger.info("MLB periodic refresh: running scheduled full ingest")
            run_ingest()
            ingest_ran = True

        if live_odds_enabled():
            ensure_odds_snapshot(game_date, force_refresh=False)

        board = build_daily_board(
            game_date=game_date,
            use_cache=False,
            refresh=True,
            skip_totals=True,
            min_edge=DEFAULT_MIN_EDGE,
            max_parlays=DEFAULT_MAX_PARLAYS,
            odds_force_refresh=False,
        )
        games = board.get("games_on_slate", len(board.get("slate", [])))
        logger.info(
            "MLB periodic refresh OK for %s (%s games, ingest=%s)",
            game_date.isoformat(),
            games,
            ingest_ran,
        )
        _write_status(
            ok=True,
            game_date=game_date,
            ingest_ran=ingest_ran,
            board_games=games,
            odds_source=board.get("odds_source"),
        )
        return 0
    except Exception as exc:
        logger.error("MLB periodic refresh failed: %s", exc, exc_info=True)
        _write_status(ok=False, game_date=game_date, error=str(exc))
        return 1
