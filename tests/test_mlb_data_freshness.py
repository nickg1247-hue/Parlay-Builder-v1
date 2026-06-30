"""Auto-ingest and odds snapshot helpers for live MLB boards."""

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd

from app.services.mlb_data_freshness import (
    ensure_mlb_ingest_fresh,
    ensure_odds_snapshot,
)


@patch("app.services.mlb_data_freshness._auto_ingest_enabled", return_value=True)
@patch("app.ingest.mlb.run_ingest")
@patch("app.services.mlb_data_freshness.get_pitcher_game_log")
@patch("app.services.mlb_data_freshness._history_gap_days", return_value=5)
def test_ensure_mlb_ingest_runs_when_history_stale(
    mock_gap, mock_log, mock_ingest, _auto
):
    mock_log.return_value = pd.DataFrame([{"x": 1}])
    out = ensure_mlb_ingest_fresh(date(2026, 6, 30), use_cache=False)
    assert out["ran"] is True
    mock_ingest.assert_called_once()


@patch("app.services.mlb_data_freshness._auto_ingest_enabled", return_value=True)
@patch("app.ingest.mlb.run_ingest")
@patch("app.services.mlb_data_freshness._history_gap_days", return_value=0)
@patch("app.services.mlb_data_freshness._file_age_days", return_value=1.0)
@patch("app.services.mlb_data_freshness.get_pitcher_game_log")
def test_ensure_mlb_ingest_skips_when_fresh(
    mock_log, _age, _gap, mock_ingest, _auto
):
    mock_log.return_value = pd.DataFrame([{"x": 1}])
    out = ensure_mlb_ingest_fresh(date(2026, 6, 30), use_cache=False)
    assert out["ran"] is False
    mock_ingest.assert_not_called()


def test_ensure_mlb_ingest_skips_demo():
    out = ensure_mlb_ingest_fresh(date(2026, 6, 30), use_cache=True)
    assert out["ran"] is False
    assert out["reason"] == "skipped"


@patch("app.services.mlb_data_freshness.live_odds_enabled", return_value=False)
def test_ensure_odds_skips_when_live_disabled(_live):
    out = ensure_odds_snapshot(date(2026, 6, 30))
    assert out["ran"] is False
    assert out["reason"] == "live_odds_disabled"


@patch("app.services.mlb_data_freshness.live_odds_enabled", return_value=True)
@patch("app.odds.odds_repository.has_date", return_value=False)
@patch("app.odds.odds_repository.get_mlb_odds_for_date")
def test_ensure_odds_fetches_when_missing(mock_fetch, _has, _live):
    mock_fetch.return_value = ([{"game_id": "1"}], "the_odds_api")
    out = ensure_odds_snapshot(date(2026, 6, 30))
    assert out["ran"] is True
    mock_fetch.assert_called_once()


@patch("app.services.mlb_data_freshness.live_odds_enabled", return_value=True)
@patch("app.services.mlb_data_freshness._odds_snapshot_age_hours", return_value=1.0)
@patch("app.odds.odds_repository.has_date", return_value=True)
@patch("app.odds.odds_repository.get_mlb_odds_for_date")
def test_ensure_odds_skips_when_fresh(mock_fetch, _has, _age, _live):
    out = ensure_odds_snapshot(date(2026, 6, 30))
    assert out["ran"] is False
    mock_fetch.assert_not_called()
