"""Per-game NBA Summer League insights — market leans + schedule (no NBA model)."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from app.odds.live_odds import live_odds_enabled
from app.services.nba_summer_daily_board import (
    DISCLAIMER,
    build_nba_summer_daily_board,
)
from app.services.schedule_nba_summer import get_nba_summer_game

logger = logging.getLogger(__name__)


def _slate_row(board: dict[str, Any], game_id: str) -> dict[str, Any] | None:
    for row in board.get("slate") or []:
        if str(row.get("game_id")) == str(game_id):
            return row
    return None


def build_nba_summer_game_insights(
    game_id: str,
    game_date: date | None = None,
    *,
    refresh: bool = False,
) -> dict[str, Any] | None:
    game_date = game_date or date.today()
    detail = get_nba_summer_game(game_id, game_date)
    if detail is None:
        return None

    board = build_nba_summer_daily_board(
        game_date=date.fromisoformat(detail["date"])
        if detail.get("date")
        else game_date,
        refresh=refresh,
        odds_force_refresh=refresh and live_odds_enabled(),
    )
    board_row = _slate_row(board, game_id)
    game = detail["game"]

    pick_team = board_row.get("model_pick_team") if board_row else None
    pick_prob = board_row.get("model_pick_prob") if board_row else None
    win_pct = round(float(pick_prob) * 100, 1) if pick_prob is not None else None

    warnings = list(board.get("warnings") or [])
    if not live_odds_enabled():
        warnings.append(
            "Live odds disabled — enable USE_LIVE_ODDS + ODDS_API_KEY for Summer League lines."
        )

    return {
        "game_id": str(game_id),
        "date": detail.get("date") or game_date.isoformat(),
        "sport": "nba-summer",
        "mode": "live",
        "odds_source": board.get("odds_source", "none"),
        "disclaimer": DISCLAIMER,
        "warnings": warnings,
        "game": game,
        "board_row": board_row,
        "model": {
            "pick": pick_team,
            "pick_side": board_row.get("model_pick_side") if board_row else None,
            "win_pct": win_pct,
            "confidence": board_row.get("model_confidence") if board_row else None,
            "pick_source": "market_implied",
            "note": (
                "Market-implied favorite from Summer League sportsbooks. "
                "Regular-season NBA model is not used."
            ),
        },
        "pick_mode": "market_implied",
        "betting_ready": False,
    }
