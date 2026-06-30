"""Shared pytest hooks."""

from unittest.mock import patch

import pytest


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
