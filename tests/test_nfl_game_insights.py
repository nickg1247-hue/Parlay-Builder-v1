"""Tests for NFL per-game insights."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services import nfl_game_insights as ngi

client = TestClient(app)

SAMPLE_GAME = {
    "game_id": "401671617",
    "home_team": "Kansas City Chiefs",
    "away_team": "Baltimore Ravens",
    "home_logo_url": "https://example.com/kc.png",
    "away_logo_url": "https://example.com/bal.png",
}

SAMPLE_BOARD_ROW = {
    "game_id": "401671617",
    "home_team": "Kansas City Chiefs",
    "away_team": "Baltimore Ravens",
    "model_prob_home": 0.62,
    "model_prob_away": 0.38,
    "market_prob_home": 0.56,
    "market_prob_away": 0.44,
    "edge_home": 0.06,
    "edge_away": -0.06,
    "ml_confidence": "Medium",
    "plus_ev_single": False,
    "model_pick": "Kansas City Chiefs",
    "model_pick_side": "home",
    "home_ml": -140,
    "away_ml": 120,
    "model_margin": 3.5,
    "model_prob_home_cover": 0.55,
    "spread_pick": "Kansas City Chiefs -3",
    "home_spread_point": -3.0,
    "spread_line_source": "book",
    "spread_confidence": "Medium",
    "expected_total_pts": 46.0,
    "model_prob_over": 0.54,
    "totals_pick": "Over 44.5",
    "totals_confidence": "Low",
    "ou_line": 44.5,
    "ou_line_source": "book",
}


@patch("app.services.nfl_game_insights.predict_slate", return_value={})
@patch("app.services.nfl_game_insights._feature_snapshot", return_value=[])
@patch("app.services.nfl_game_insights.build_nfl_daily_board")
@patch("app.services.nfl_game_insights.get_nfl_game")
def test_build_nfl_game_insights_success(mock_game, mock_board, _mock_feats, _mock_pred):
    mock_game.return_value = {
        "game": SAMPLE_GAME,
        "date": "2025-09-07",
        "resolved_date": "2025-09-07",
    }
    mock_board.return_value = {
        "mode": "demo",
        "odds_source": "espn_scoreboard",
        "warnings": [],
        "edge_threshold": 0.08,
        "active_moneyline_model": {
            "model_version": "v1_logistic_platt",
            "feature_set": "nfl_v1",
        },
        "slate": [SAMPLE_BOARD_ROW],
        "parlays": [],
    }

    result = ngi.build_nfl_game_insights(
        "401671617", game_date=date(2025, 9, 7), use_cache=True
    )

    assert result is not None
    assert result["sport"] == "nfl"
    assert result["date"] == "2025-09-07"
    assert result["game"]["game_id"] == "401671617"
    assert result["moneyline"]["home_ml"] == -140
    assert result["moneyline"]["model_pick"] == "Kansas City Chiefs"
    assert result["spread"]["spread_pick"] == "Kansas City Chiefs -3"
    assert result["spread"]["model_prob_home_cover"] == 0.55
    assert result["totals"]["totals_pick"] == "Over 44.5"
    assert result["matchup_board"]["home"]["moneyline"] == -140
    assert result["matchup_board"]["away"]["spread"] == 3.0
    assert result["matchup_board"]["highlights"]["moneyline_side"] == "home"
    assert result["betting_ready"] is False
    assert "betting_ready=false" in result["disclaimer"]
    assert result["active_model"]["model_version"] == "v1_logistic_platt"
    assert result["feature_snapshot"] == []


@patch("app.services.nfl_game_insights.get_nfl_game", return_value=None)
def test_build_nfl_game_insights_not_found(_mock_game):
    assert ngi.build_nfl_game_insights("999", game_date=date(2025, 9, 7)) is None


@patch("app.services.nfl_game_insights.build_nfl_game_insights")
def test_nfl_insights_api_not_found(mock_insights):
    mock_insights.return_value = None
    resp = client.get("/api/games/nfl/999/insights?date=2025-09-07")
    assert resp.status_code == 404


@patch("app.services.nfl_game_insights.build_nfl_game_insights")
def test_nfl_insights_api_success(mock_insights):
    mock_insights.return_value = {
        "game": SAMPLE_GAME,
        "date": "2025-09-07",
        "sport": "nfl",
        "moneyline": {"model_pick": "Kansas City Chiefs"},
        "spread": {},
        "totals": {},
        "matchup_board": {"home": {}, "away": {}, "highlights": {}},
        "feature_snapshot": [],
        "warnings": [],
        "betting_ready": False,
        "disclaimer": "test",
        "active_model": {},
        "odds_source": "espn_scoreboard",
        "parlays": [],
    }
    resp = client.get("/api/games/nfl/401671617/insights?date=2025-09-07&use_cache=true")
    assert resp.status_code == 200
    assert resp.json()["sport"] == "nfl"
    mock_insights.assert_called_once()


def test_nfl_game_page():
    resp = client.get("/nfl/game/401671617")
    assert resp.status_code == 200
    assert "NFL Game" in resp.text
    assert "nfl_game.js" in resp.text
    assert "feature-snapshot" in resp.text
