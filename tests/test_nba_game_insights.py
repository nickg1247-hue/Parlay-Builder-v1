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
    "model_margin": 4.2,
    "spread_best_pick": {
        "side": "home",
        "team": "Boston Celtics",
        "edge": 0.09,
        "american_odds": -110,
        "spread_point": -5.5,
    },
    "expected_total_pts": 228.5,
    "model_prob_over": 0.58,
    "totals_pick": "Over 224.5",
    "total_edge": 0.08,
    "totals_confidence": "Medium",
}


@patch("app.services.nba_game_insights.build_game_prediction_detail")
@patch("app.services.nba_game_insights.build_nba_daily_board")
@patch("app.services.nba_game_insights.get_nba_game")
@patch("app.services.nba_game_insights._nba_sportsbook_lines")
def test_build_nba_game_insights_success(mock_lines, mock_game, mock_board, mock_pred):
    mock_game.return_value = {"game": SAMPLE_GAME}
    mock_pred.return_value = {
        "model_version": "v2_test",
        "feature_count": 22,
        "drivers": ["Recent form favors Boston Celtics"],
        "features": {"home_last10_win_pct": 0.7},
    }
    mock_board.return_value = {
        "mode": "live",
        "odds_source": "the_odds_api",
        "warnings": [],
        "edge_threshold": 0.08,
        "board_spread_enabled": True,
        "board_totals_enabled": True,
        "slate": [SAMPLE_BOARD_ROW],
    }
    mock_lines.return_value = {
        "source": "the_odds_api",
        "home_ml": -150,
        "away_ml": 130,
        "total_line": 224.5,
        "over_am": -110,
        "under_am": -110,
        "home_spread": {"point": -5.5, "american": -110},
        "away_spread": {"point": 5.5, "american": -110},
    }

    result = ngi.build_nba_game_insights(
        "401766458", game_date=date(2026, 4, 10), use_cache=False
    )

    assert result is not None
    assert result["game_id"] == "401766458"
    assert result["market_cards"]["away"]["moneyline_american"] == 130
    assert result["market_cards"]["home"]["moneyline_american"] == -150
    assert result["market_cards"]["total"]["line"] == 224.5
    assert result["model"]["pick"] == "Boston Celtics"
    assert result["model"]["model_margin"] == 4.2
    assert result["model"]["model_total_pts"] == 228.5
    assert result["model"]["spread_pick"] == "Boston Celtics -5.5"
    assert result["board_spread_enabled"] is True
    assert result["board_totals_enabled"] is True
    assert result["betting_ready"] is False
    assert result["prediction"]["model_version"] == "v2_test"
    assert len(result["prediction"]["drivers"]) >= 1
    mock_board.assert_called_once()
    assert mock_board.call_args.kwargs.get("skip_totals") is False


@patch("app.services.nba_game_insights.build_game_prediction_detail")
@patch("app.services.nba_game_insights.build_nba_daily_board")
@patch("app.services.nba_game_insights.get_nba_game")
@patch("app.services.nba_game_insights._nba_sportsbook_lines")
def test_build_nba_game_insights_model_without_odds(mock_lines, mock_game, mock_board, mock_pred):
    mock_game.return_value = {"game": SAMPLE_GAME}
    mock_pred.return_value = {"model_version": "v2_test", "drivers": [], "features": {}}
    mock_board.return_value = {
        "mode": "live",
        "odds_source": "none",
        "warnings": [],
        "edge_threshold": 0.08,
        "slate": [SAMPLE_BOARD_ROW],
    }
    mock_lines.return_value = {
        "source": "none",
        "home_ml": None,
        "away_ml": None,
        "total_line": None,
        "over_am": None,
        "under_am": None,
        "home_spread": {"point": None, "american": None},
        "away_spread": {"point": None, "american": None},
    }

    result = ngi.build_nba_game_insights(
        "401766458", game_date=date(2026, 6, 8), use_cache=False
    )

    assert result["model"]["pick"] == "Boston Celtics"
    assert result["model"]["win_pct"] == 62.0
    assert len(result["warnings"]) >= 1


@patch("app.services.nba_game_insights._lines_from_cached_date")
@patch("app.services.nba_game_insights.live_odds_enabled", return_value=False)
@patch("app.services.nba_game_insights.has_date", return_value=False)
def test_sportsbook_lines_use_csv_without_use_cache(
    _has_date, _live, mock_cached
):
    mock_cached.return_value = {
        "source": "historical_cache",
        "home_ml": -130,
        "away_ml": 110,
        "total_line": None,
        "over_am": None,
        "under_am": None,
        "home_spread": {"point": None, "american": None},
        "away_spread": {"point": None, "american": None},
    }
    lines = ngi._nba_sportsbook_lines(
        SAMPLE_GAME,
        date(2026, 4, 10),
        use_cache=False,
    )
    assert lines["source"] == "historical_cache"
    assert lines["home_ml"] == -130
    mock_cached.assert_called_once()


@patch("app.services.nba_game_insights.get_nba_game", return_value=None)
def test_build_nba_game_insights_not_found(_mock_game):
    assert ngi.build_nba_game_insights("999", game_date=date(2026, 4, 10)) is None


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
        "market_cards": {
            "source": "none",
            "away": {},
            "home": {},
            "total": {},
        },
        "model": {"pick": "Boston Celtics"},
        "highlights": {},
        "board_spread_enabled": False,
        "board_totals_enabled": False,
        "warnings": [],
    }
    resp = client.get(
        "/api/games/nba/401766458/insights?date=2026-04-10&use_cache=true"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["game_id"] == "401766458"
    assert "board_totals_enabled" in body
    mock_insights.assert_called_once()
