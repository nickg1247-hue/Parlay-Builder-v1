"""Expose per-game weighted-factor model inputs and drivers."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.models.nba_custom import build_prediction_detail as build_custom_prediction_detail


def build_game_prediction_detail(
    game: dict[str, Any],
    game_date: date,
) -> dict[str, Any]:
    return build_custom_prediction_detail(game, game_date)


def build_prediction_drivers(feat: dict[str, Any]) -> list[str]:
    """Legacy helper — drivers are built inside build_game_prediction_detail."""
    del feat
    return []
