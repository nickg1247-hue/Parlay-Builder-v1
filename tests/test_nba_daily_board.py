"""Tests for NBA daily board API and demo cache path."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import nba_daily_board as ndb

client = TestClient(app)


@pytest.fixture
def sample_nba_schedule():
    return {
        "date": "2026-04-10",
        "games": [
            {
                "game_id": "401766458",
                "home_team": "Boston Celtics",
                "away_team": "New York Knicks",
            },
            {
                "game_id": "401766459",
                "home_team": "Los Angeles Lakers",
                "away_team": "Golden State Warriors",
            },
        ],
    }


@patch("app.services.nba_daily_board.get_nba_schedule")
@patch("app.services.nba_daily_board.predict_home_win_proba")
@patch("app.services.nba_daily_board.load_odds_for_date")
@patch("app.services.nba_daily_board.get_nba_odds_for_date")
def test_use_cache_skips_live_api(
    mock_live_odds,
    mock_cached_odds,
    mock_predict,
    mock_get_schedule,
    sample_nba_schedule,
):
    mock_get_schedule.return_value = sample_nba_schedule
    mock_predict.return_value = pd.Series([0.55, 0.48])
    mock_cached_odds.return_value = (pd.DataFrame(), "none")

    resp = client.get(
        "/api/nba/daily?date=2026-04-10&use_cache=true&min_edge=0.08"
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "demo"
    assert body["betting_ready"] is False
    assert body["odds_source"] == "none"
    mock_cached_odds.assert_called_once()
    mock_live_odds.assert_not_called()


@patch("app.services.nba_daily_board.get_nba_schedule")
@patch("app.services.nba_daily_board.predict_home_win_proba")
@patch("app.services.nba_daily_board.load_odds_for_date")
def test_use_cache_attaches_csv_odds(
    mock_cached_odds,
    mock_predict,
    mock_get_schedule,
    sample_nba_schedule,
):
    mock_get_schedule.return_value = sample_nba_schedule
    mock_predict.return_value = pd.Series([0.70, 0.48])
    odds_df = pd.DataFrame(
        [
            {
                "date": "2026-04-10",
                "home_team": "Boston Celtics",
                "away_team": "New York Knicks",
                "home_ml": -130,
                "away_ml": 110,
            }
        ]
    )
    mock_cached_odds.return_value = (odds_df, "historical_cache")

    resp = client.get(
        "/api/nba/daily?date=2026-04-10&use_cache=true&min_edge=0.08"
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "demo"
    assert body["odds_source"] == "historical_cache"
    assert body["games_with_odds"] == 1
    row = body["slate"][0]
    assert row["market_prob_home"] is not None
    assert row["ml_edge_best"] is not None


@patch("app.services.nba_daily_board.get_nba_schedule")
@patch("app.services.nba_daily_board.predict_home_win_proba")
@patch("app.services.nba_daily_board.get_nba_odds_for_date")
def test_live_refresh_calls_repository(
    mock_live_odds,
    mock_predict,
    mock_get_schedule,
    sample_nba_schedule,
):
    mock_get_schedule.return_value = sample_nba_schedule
    mock_predict.return_value = pd.Series([0.55, 0.48])
    mock_live_odds.return_value = ([], "none")

    resp = client.get("/api/nba/daily?refresh=true&min_edge=0.08")

    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "live"
    mock_live_odds.assert_called_once()
    _, kwargs = mock_live_odds.call_args
    assert kwargs.get("force_refresh") is True


def test_build_board_demo_mode_field():
    with (
        patch.object(ndb, "get_nba_schedule") as mock_sched,
        patch.object(ndb, "predict_home_win_proba") as mock_pred,
        patch.object(ndb, "_attach_cached_odds") as mock_attach,
    ):
        mock_sched.return_value = {
            "games": [
                {
                    "game_id": "1",
                    "home_team": "Boston Celtics",
                    "away_team": "New York Knicks",
                }
            ]
        }
        mock_pred.return_value = pd.Series([0.6])
        merged = pd.DataFrame(
            [
                {
                    "game_id": "1",
                    "date": "2026-04-10",
                    "season": 2026,
                    "home_team": "Boston Celtics",
                    "away_team": "New York Knicks",
                    "model_prob_home": 0.6,
                    "model_prob_away": 0.4,
                    "home_ml": float("nan"),
                    "away_ml": float("nan"),
                }
            ]
        )
        mock_attach.return_value = (merged, "none")

        result = ndb.build_nba_daily_board(
            date(2026, 4, 10), use_cache=True, log_clv=False
        )

    assert result["mode"] == "demo"
    assert result["disclaimer"]
    assert result["betting_ready"] is False
    mock_attach.assert_called_once()
