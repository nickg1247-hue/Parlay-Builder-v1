"""Tests for CFB per-game insights."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services import cfb_game_insights as cgi

client = TestClient(app)

SAMPLE_GAME = {
    "game_id": "401635000",
    "home_team": "Georgia",
    "away_team": "Georgia Tech",
    "home_logo_url": "https://example.com/uga.png",
    "away_logo_url": "https://example.com/gt.png",
}

SAMPLE_BOARD_ROW = {
    "game_id": "401635000",
    "home_team": "Georgia",
    "away_team": "Georgia Tech",
    "model_prob_home": 0.78,
    "model_prob_away": 0.22,
    "market_prob_home": 0.72,
    "market_prob_away": 0.28,
    "edge_home": 0.06,
    "edge_away": -0.06,
    "ml_confidence": "Medium",
    "plus_ev_single": False,
    "model_pick": "Georgia",
    "model_pick_side": "home",
    "home_ml": -350,
    "away_ml": 280,
    "model_margin": 14.2,
    "model_prob_home_cover": 0.62,
    "spread_pick": "Georgia -7",
    "home_spread_point": -7.0,
    "spread_line_source": "book",
    "spread_confidence": "High",
    "expected_total_pts": 55.0,
    "model_prob_over": 0.58,
    "totals_pick": "Over 51.5",
    "totals_confidence": "Medium",
    "ou_line": 51.5,
    "ou_line_source": "book",
}


@patch("app.services.cfb_game_insights._pred_row", return_value=None)
@patch("app.services.cfb_game_insights._build_feature_snapshot", return_value=[])
@patch("app.services.cfb_game_insights.build_cfb_daily_board")
@patch("app.services.cfb_game_insights.get_cfb_game")
def test_build_cfb_game_insights_success(mock_game, mock_board, _mock_feats, _mock_pred):
    mock_game.return_value = {
        "game": SAMPLE_GAME,
        "date": "2024-11-30",
        "resolved_date": "2024-11-30",
    }
    mock_board.return_value = {
        "mode": "demo",
        "odds_source": "cfbd_lines",
        "warnings": [],
        "edge_threshold": 0.08,
        "active_moneyline_model": {
            "model_version": "v3_logistic_platt",
            "feature_set": "cfb_v3",
        },
        "slate": [SAMPLE_BOARD_ROW],
    }

    result = cgi.build_cfb_game_insights(
        "401635000", game_date=date(2024, 11, 30), use_cache=True
    )

    assert result is not None
    assert result["sport"] == "cfb"
    assert result["date"] == "2024-11-30"
    assert result["game"]["game_id"] == "401635000"
    assert result["moneyline"]["home_ml"] == -350
    assert result["moneyline"]["model_pick"] == "Georgia"
    assert result["spread"]["spread_pick"] == "Georgia -7"
    assert result["spread"]["model_prob_home_cover"] == 0.62
    assert result["totals"]["totals_pick"] == "Over 51.5"
    assert result["matchup_board"]["home"]["moneyline"] == -350
    assert result["matchup_board"]["away"]["spread"] == 7.0
    assert result["matchup_board"]["highlights"]["moneyline_side"] == "home"
    assert result["betting_ready"] is False
    assert "betting_ready=false" in result["disclaimer"]
    assert result["active_model"]["model_version"] == "v3_logistic_platt"
    assert result["feature_snapshot"] == []


@patch("app.services.cfb_game_insights._pred_row", return_value=None)
@patch("app.services.cfb_game_insights._build_feature_snapshot", return_value=[])
@patch("app.services.cfb_game_insights.build_cfb_daily_board")
@patch("app.services.cfb_game_insights.get_cfb_game")
def test_build_cfb_game_insights_model_without_odds(mock_game, mock_board, _mock_feats, _mock_pred):
    mock_game.return_value = {
        "game": SAMPLE_GAME,
        "date": "2024-11-30",
        "resolved_date": "2024-11-30",
    }
    row = dict(SAMPLE_BOARD_ROW)
    row["home_ml"] = None
    row["away_ml"] = None
    mock_board.return_value = {
        "mode": "live",
        "odds_source": "none",
        "warnings": [],
        "edge_threshold": 0.08,
        "slate": [row],
    }

    result = cgi.build_cfb_game_insights(
        "401635000", game_date=date(2024, 11, 30), use_cache=False
    )

    assert result["moneyline"]["model_pick"] == "Georgia"
    assert result["moneyline"]["model_prob_home"] == 0.78
    assert len(result["warnings"]) >= 1


@patch("app.services.cfb_game_insights.get_cfb_game", return_value=None)
def test_build_cfb_game_insights_not_found(_mock_game):
    assert cgi.build_cfb_game_insights("999", game_date=date(2024, 11, 30)) is None


@patch("app.services.cfb_game_insights.build_cfb_game_insights")
def test_cfb_insights_api_not_found(mock_insights):
    mock_insights.return_value = None
    resp = client.get("/api/games/cfb/999/insights?date=2024-11-30")
    assert resp.status_code == 404


@patch("app.services.cfb_game_insights.build_cfb_game_insights")
def test_cfb_insights_api_success(mock_insights):
    mock_insights.return_value = {
        "game": SAMPLE_GAME,
        "date": "2024-11-30",
        "sport": "cfb",
        "moneyline": {"model_pick": "Georgia"},
        "spread": {},
        "totals": {},
        "matchup_board": {"home": {}, "away": {}, "highlights": {}},
        "feature_snapshot": [],
        "warnings": [],
        "betting_ready": False,
        "disclaimer": "test",
        "active_model": {},
        "odds_source": "cfbd_lines",
    }
    resp = client.get(
        "/api/games/cfb/401635000/insights?date=2024-11-30&use_cache=true"
    )
    assert resp.status_code == 200
    assert resp.json()["sport"] == "cfb"
    mock_insights.assert_called_once()


def test_cfb_game_page():
    resp = client.get("/cfb/game/401635000")
    assert resp.status_code == 200
    assert "CFB Game" in resp.text
    assert "cfb_game.js" in resp.text
    assert "feature-snapshot" in resp.text
