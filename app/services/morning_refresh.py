"""Morning refresh: pre-build daily board and schedule cache."""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.config import PROJECT_ROOT
from app.models.constants import DEFAULT_MIN_EDGE
from app.parlay.ev_ranker import DEFAULT_MAX_PARLAYS
from app.odds.live_odds import live_odds_enabled
from app.odds.odds_repository import get_mlb_odds_for_date, last_fetch_meta
from app.services.daily_board import build_daily_board
from app.services.schedule_mlb import refresh_schedule_cache

logger = logging.getLogger(__name__)

LAST_MORNING_REFRESH = PROJECT_ROOT / "data" / "processed" / "last_morning_refresh.json"

_TRANSIENT_ERRORS = (
    httpx.HTTPError,
    httpx.TimeoutException,
    ConnectionError,
    OSError,
)

_DEFAULT_STATUS: dict[str, Any] = {
    "ran_at": None,
    "ok": False,
    "date": None,
    "games_on_slate": None,
    "odds_source": None,
    "error": "No morning refresh has run yet",
}


def _parse_iso_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts
    except (TypeError, ValueError):
        return None


def _display_refresh_timestamp(status: dict[str, Any]) -> tuple[str | None, str | None]:
    """Most recent refresh moment across board, odds, hourly job, and props cache."""
    candidates: list[tuple[datetime, str]] = []
    for key, label in (
        ("ran_at", "board"),
        ("odds_fetched_at", "odds"),
        ("props_cached_at", "props"),
    ):
        ts = _parse_iso_utc(status.get(key))
        if ts is not None:
            candidates.append((ts, label))
    hourly = status.get("hourly_last") or {}
    if hourly.get("ok") and not hourly.get("skipped"):
        ts = _parse_iso_utc(hourly.get("ran_at"))
        if ts is not None:
            candidates.append((ts, "hourly_odds"))
    if not candidates:
        return None, None
    latest = max(candidates, key=lambda item: item[0])
    return latest[0].isoformat(), latest[1]


def _morning_skip_totals() -> bool:
    raw = os.getenv("MORNING_SKIP_TOTALS", "true").strip().lower()
    return raw not in ("0", "false", "no")


def get_refresh_status() -> dict[str, Any]:
    """Return morning refresh status plus live odds repository snapshot."""
    if LAST_MORNING_REFRESH.exists():
        try:
            status = json.loads(LAST_MORNING_REFRESH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read %s: %s", LAST_MORNING_REFRESH, exc)
            status = {
                **_DEFAULT_STATUS,
                "error": f"Status file unreadable: {exc}",
            }
    else:
        status = dict(_DEFAULT_STATUS)

    from app.odds.odds_repository import get_today_snapshot
    from app.services.odds_hourly_refresh import (
        hourly_refresh_enabled,
        load_hourly_refresh_status,
    )
    from app.services.prop_tracker_refresh import load_prop_tracker_refresh_status

    snap = get_today_snapshot()
    status["odds_fetched_at"] = snap.get("fetched_at")
    status["odds_seconds_since_fetch"] = snap.get("seconds_since_fetch")
    status["odds_repo_source"] = snap.get("source")
    status["hourly_refresh_enabled"] = hourly_refresh_enabled()
    hourly = load_hourly_refresh_status()
    if hourly is not None:
        status["hourly_last"] = hourly

    from app.services.props_mlb import get_props_refresh_meta

    props_meta = get_props_refresh_meta(date.today())
    status["props_cached_at"] = props_meta.get("cached_at")
    status["props_actionable_count"] = props_meta.get("total_actionable", 0)

    prop_tracker = load_prop_tracker_refresh_status()
    if prop_tracker is not None:
        status["prop_tracker_last"] = prop_tracker

    display_at, display_source = _display_refresh_timestamp(status)
    status["display_updated_at"] = display_at
    status["display_source"] = display_source
    return status


def _write_status(
    *,
    ok: bool,
    game_date: date,
    games_on_slate: int | None = None,
    odds_source: str | None = None,
    error: str | None = None,
    odds_quota_warning: str | None = None,
) -> None:
    payload = {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "ok": ok,
        "date": game_date.isoformat(),
        "games_on_slate": games_on_slate,
        "odds_source": odds_source,
        "error": error,
        "odds_quota_warning": odds_quota_warning,
    }
    LAST_MORNING_REFRESH.parent.mkdir(parents=True, exist_ok=True)
    LAST_MORNING_REFRESH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _build_board_with_retry(game_date: date) -> dict[str, Any]:
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            return build_daily_board(
                game_date=game_date,
                use_cache=False,
                refresh=True,
                skip_totals=_morning_skip_totals(),
                min_edge=DEFAULT_MIN_EDGE,
                max_parlays=DEFAULT_MAX_PARLAYS,
                odds_force_refresh=False,
            )
        except _TRANSIENT_ERRORS as exc:
            last_exc = exc
            if attempt == 0:
                logger.warning(
                    "Transient network error (attempt 1/2): %s — retrying in 30s",
                    exc,
                )
                time.sleep(30)
            else:
                raise
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("build_daily_board retry loop exited unexpectedly")


def run_morning_refresh(
    game_date: date | None = None,
    sports: list[str] | None = None,
) -> int:
    """Run morning refresh. Returns 0 on success, 1 on failure."""
    game_date = game_date or date.today()
    sport_list = [s.lower() for s in (sports or ["mlb"])]
    if not sport_list:
        sport_list = ["mlb"]

    if not os.getenv("ODDS_API_KEY", "").strip():
        logger.warning(
            "ODDS_API_KEY not set — building model-only board without live odds. "
            "Add your free key to .env; see DEV.md."
        )

    odds_quota_warning: str | None = None
    games_on_slate: int | None = None
    odds_source: str | None = None
    try:
        if "mlb" in sport_list:
            if live_odds_enabled():
                get_mlb_odds_for_date(game_date, force_refresh=True)
                meta = last_fetch_meta()
                if meta.get("quota_warning"):
                    odds_quota_warning = meta["quota_warning"]

            board = _build_board_with_retry(game_date)
            games_on_slate = board.get("games_on_slate", len(board.get("slate", [])))
            odds_source = board.get("odds_source", "none")
            refresh_schedule_cache(game_date)

            from app.services.props_mlb import build_daily_top_props

            if live_odds_enabled():
                try:
                    props_out = build_daily_top_props(
                        game_date,
                        limit=50,
                        scan=True,
                        refresh=True,
                    )
                    logger.info(
                        "Morning props scan: %s actionable props (%s games fetched)",
                        props_out.get("total_actionable", 0),
                        props_out.get("games_fetched", 0),
                    )
                except Exception as exc:
                    logger.warning("Morning props scan failed: %s", exc)

        if "nba" in sport_list:
            from app.services.schedule_nba import refresh_schedule_cache as refresh_nba

            nba_payload = refresh_nba(game_date)
            if games_on_slate is None:
                games_on_slate = nba_payload.get("games_count", 0)

        _write_status(
            ok=True,
            game_date=game_date,
            games_on_slate=games_on_slate,
            odds_source=odds_source,
            error=None,
            odds_quota_warning=odds_quota_warning,
        )
        logger.info(
            "Morning refresh OK: date=%s sports=%s games=%s odds_source=%s",
            game_date.isoformat(),
            ",".join(sport_list),
            games_on_slate,
            odds_source,
        )
        return 0
    except Exception as exc:
        logger.error("Morning refresh failed: %s", exc, exc_info=True)
        _write_status(
            ok=False,
            game_date=game_date,
            games_on_slate=None,
            odds_source=None,
            error=str(exc),
        )
        return 1
