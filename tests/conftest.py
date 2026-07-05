"""Shared pytest hooks."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.config import PROJECT_ROOT

PROCESSED = PROJECT_ROOT / "data" / "processed"


def latest_cached_mlb_game() -> tuple[str, str]:
    """Return (iso_date, game_id) from the newest schedule cache with games."""
    paths = sorted(PROCESSED.glob("mlb_schedule_*.json"), reverse=True)
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        games = payload.get("games") or []
        if games:
            game_date = str(payload.get("date") or path.stem.replace("mlb_schedule_", ""))
            return game_date, str(games[0]["game_id"])
    pytest.skip("no cached MLB schedule with games")


@pytest.fixture
def cached_mlb_game() -> tuple[str, str]:
    return latest_cached_mlb_game()


@pytest.fixture(autouse=True)
def _no_auto_mlb_ingest_during_board_builds():
    """Prevent live daily board builds from running ingest/odds HTTP mid-test."""
    with (
        patch(
            "app.services.daily_board.ensure_mlb_ingest_fresh",
            return_value={"ran": False, "reason": "test"},
        ),
        patch(
            "app.services.daily_board.ensure_odds_snapshot",
            return_value={"ran": False, "reason": "test"},
        ),
    ):
        yield
