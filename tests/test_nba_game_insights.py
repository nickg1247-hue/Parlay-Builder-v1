"""Tests for NBA per-game insights."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services import nba_game_insights as ngi

client = TestClient(app)

SAMPLE_GAME = {
    "game_id": "401766458",
    "home_team": "Boston Celtics",
    "away_team": "New York Knicks",
    "home_team_id": "2",
    "away_team_id": "18",
}

SAMPLE_BOARD_ROW = {
    "game_id": "401766458",
    "home_team": "Boston Celtics",
    "away_team": "New York Knicks",
    "model_prob_home": 0.62,
    "market_prob_home": 0.55,
    "ml_edge_best": 0.07,
    "ml_confidence": "Medium",
    "plus_ev_single": False,
    "best_pick": None,
}


@patch("app.services.nba_game_insights.build_nba_daily_board")
@patch("app.services.nba_game_insights.get_nba_game")
@patch("app.services.nba_game_insights._nba_sportsbook_lines")
def test_build_nba_game_insights_success(mock_lines, mock_game, mock_board):
    mock_game.return_value = {"game": SAMPLE_GAME}
    mock_board.return_value = {
        "mode": "live",
        "odds_source": "the_odds_api",
        "warnings": [],
        "edge_threshold": 0.08,
        "slate": [SAMPLE_BOARD_ROW],
    }
    mock_lines.return_value = {
        "source": "the_odds_api",
        "home_ml": -150,
        "away_ml": 130,
    }

    result = ngi.build_nba_game_insights(
        "401766458", game_date=date(2026, 4, 10), use_cache=False
    )

    assert result is not None
    assert result["game_id"] == "401766458"
    assert result["market_cards"]["away"]["moneyline_american"] == 130
    assert result["market_cards"]["home"]["moneyline_american"] == -150
    assert result["model"]["pick"] == "Boston Celtics"
    assert result["betting_ready"] is False


@patch("app.services.nba_game_insights.get_nba_game", return_value=None)
def test_build_nba_game_insights_not_found(_mock_game):
    assert ngi.build_nba_game_insights("999", game_date=date(2026, 4, 10)) is None


@patch("app.services.nba_game_insights.build_nba_daily_board")
@patch("app.services.nba_game_insights.get_nba_game")
@patch("app.services.nba_game_insights._nba_sportsbook_lines")
def test_build_nba_game_insights_warns_without_lines(mock_lines, mock_game, mock_board):
    mock_game.return_value = {"game": SAMPLE_GAME}
    mock_board.return_value = {
        "mode": "live",
        "odds_source": "none",
        "warnings": [],
        "edge_threshold": 0.08,
        "slate": [SAMPLE_BOARD_ROW],
    }
    mock_lines.return_value = {"source": "none", "home_ml": None, "away_ml": None}

    result = ngi.build_nba_game_insights(
        "401766458", game_date=date(2026, 6, 8), use_cache=False
    )

    assert any("Market lines unavailable" in w for w in result["warnings"])


@patch("app.services.nba_game_insights.build_nba_game_insights")
def test_nba_insights_api_not_found(mock_insights):
    mock_insights.return_value = None
    resp = client.get("/api/games/nba/999/insights?date=2026-04-10")
    assert resp.status_code == 404


@patch("app.services.nba_game_insights.build_nba_game_insights")
def test_nba_insights_api_success(mock_insights):
    mock_insights.return_value = {
        "game_id": "401766458",
        "date": "2026-04-10",
        "game": SAMPLE_GAME,
        "market_cards": {"source": "none", "away": {}, "home": {}},
        "model": {"pick": "Boston Celtics"},
        "warnings": [],
    }
    resp = client.get(
        "/api/games/nba/401766458/insights?date=2026-04-10&use_cache=true"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["game_id"] == "401766458"
    mock_insights.assert_called_once()
