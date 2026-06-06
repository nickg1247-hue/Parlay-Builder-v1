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


def get_refresh_status() -> dict[str, Any]:
    """Return last morning refresh status or a sensible default."""
    if not LAST_MORNING_REFRESH.exists():
        return dict(_DEFAULT_STATUS)
    try:
        return json.loads(LAST_MORNING_REFRESH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read %s: %s", LAST_MORNING_REFRESH, exc)
        return {
            **_DEFAULT_STATUS,
            "error": f"Status file unreadable: {exc}",
        }


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
                skip_totals=False,
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
