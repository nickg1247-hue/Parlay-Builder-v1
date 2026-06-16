"""Lightweight home-page summary from morning board cache."""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT
from app.odds.odds_repository import get_today_snapshot
from app.services.daily_board import DAILY_BOARD_CACHE

logger = logging.getLogger(__name__)

STATIC_COLORS = PROJECT_ROOT / "static" / "mlb_team_colors.json"


def _load_board() -> dict[str, Any] | None:
    if not DAILY_BOARD_CACHE.exists():
        return None
    try:
        return json.loads(DAILY_BOARD_CACHE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read daily board for home summary: %s", exc)
        return None


def _slate_index(slate: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in slate:
        gid = str(row.get("game_id", ""))
        if not gid:
            continue
        best = row.get("best_pick")
        out[gid] = {
            "game_id": gid,
            "matchup": row.get("matchup"),
            "away_team": row.get("away_team"),
            "home_team": row.get("home_team"),
            "best_pick": best,
            "model_pick_team": row.get("model_pick_team"),
            "model_pick_side": row.get("model_pick_side"),
            "model_confidence": row.get("model_confidence"),
            "ev_pick_team": row.get("ev_pick_team"),
            "ev_pick_edge": row.get("ev_pick_edge"),
            "totals_pick": row.get("totals_pick"),
            "ml_confidence": row.get("ml_confidence"),
            "totals_confidence": row.get("totals_confidence"),
            "expected_total_runs": row.get("expected_total_runs"),
            "ou_line": row.get("ou_line"),
            "plus_ev_single": row.get("plus_ev_single", False),
            "plus_ev_total": row.get("plus_ev_total", False),
            "model_prob_home": row.get("model_prob_home"),
            "home_ml": row.get("home_ml"),
            "away_ml": row.get("away_ml"),
        }
    return out


def get_home_today_summary(game_date: date | None = None) -> dict[str, Any]:
    """Today at a glance + best bets from on-disk daily board (no rebuild)."""
    game_date = game_date or date.today()
    board = _load_board()
    odds_snap = get_today_snapshot()

    empty: dict[str, Any] = {
        "date": game_date.isoformat(),
        "board_available": False,
        "games_on_slate": 0,
        "games_with_odds": 0,
        "plus_ev_singles": 0,
        "plus_ev_totals": 0,
        "top_singles": [],
        "slate_by_game_id": {},
        "odds_fetched_at": odds_snap.get("fetched_at"),
        "odds_source": board.get("odds_source") if board else None,
        "message": "Run morning refresh or board Run live to populate picks.",
    }

    if board is None or board.get("date") != game_date.isoformat():
        return empty

    slate = board.get("slate") or []
    plus_ev_singles = sum(1 for g in slate if g.get("plus_ev_single"))
    plus_ev_totals = sum(1 for g in slate if g.get("plus_ev_total"))

    return {
        "date": board.get("date", game_date.isoformat()),
        "board_available": True,
        "generated_at": board.get("generated_at"),
        "games_on_slate": board.get("games_on_slate", len(slate)),
        "games_with_odds": board.get("games_with_odds", 0),
        "plus_ev_singles": plus_ev_singles,
        "plus_ev_totals": plus_ev_totals,
        "top_singles": (board.get("top_singles") or [])[:5],
        "slate_by_game_id": _slate_index(slate),
        "odds_fetched_at": odds_snap.get("fetched_at"),
        "odds_source": board.get("odds_source"),
        "message": None,
    }
