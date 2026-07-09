"""Tests for UFC per-fight insights and pages."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services import ufc_fight_insights as ufi

client = TestClient(app)

SAMPLE_FIGHT = {
    "game_id": "401613930",
    "fight_id": "401613930",
    "home_team": "Magomed Ankalaev",
    "away_team": "Johnny Walker",
    "weight_class": "Light Heavyweight",
    "status": "Final",
}

SAMPLE_BOARD_ROW = {
    "fight_id": "401613930",
    "game_id": "401613930",
    "home_team": "Magomed Ankalaev",
    "away_team": "Johnny Walker",
    "model_prob_home": 0.62,
    "model_prob_away": 0.38,
    "market_prob_home": 0.55,
    "market_prob_away": 0.45,
    "edge_home": 0.07,
    "edge_away": -0.07,
    "ml_confidence": "Medium",
    "plus_ev_single": False,
    "model_pick": "Magomed Ankalaev",
    "model_pick_side": "home",
    "home_ml": -180,
    "away_ml": 155,
}


@patch("app.services.ufc_fight_insights.predict_matchup", return_value=None)
@patch("app.services.ufc_fight_insights._build_feature_snapshot", return_value=[])
@patch("app.services.ufc_fight_insights._build_fighter_stats")
@patch("app.services.ufc_fight_insights._pred_row", return_value=None)
@patch("app.services.ufc_fight_insights.build_ufc_daily_board")
@patch("app.services.ufc_fight_insights.get_ufc_fight")
def test_build_ufc_fight_insights_success(
    mock_fight, mock_board, _mock_pred, mock_stats, _mock_feats, _mock_matchup
):
    mock_fight.return_value = {
        "game": SAMPLE_FIGHT,
        "date": "2024-01-13",
        "resolved_date": "2024-01-13",
    }
    mock_board.return_value = {
        "mode": "demo",
        "odds_source": "repository",
        "warnings": [],
        "edge_threshold": 0.08,
        "active_moneyline_model": {
            "model_version": "ufc_v1_logistic_platt",
            "feature_set": "ufc_baseline",
        },
        "slate": [SAMPLE_BOARD_ROW],
        "top_parlays": [],
    }
    mock_stats.return_value = {
        "home": {"name": "Magomed Ankalaev", "layoff_label": "90 days since last fight"},
        "away": {"name": "Johnny Walker", "layoff_label": "4 mo since last fight"},
        "weight_class": "Light Heavyweight",
    }

    result = ufi.build_ufc_fight_insights(
        "401613930", game_date=date(2024, 1, 13), use_cache=True
    )

    assert result is not None
    assert result["sport"] == "ufc"
    assert result["moneyline"]["model_pick"] == "Magomed Ankalaev"
    assert result["fighter_stats"]["home"]["layoff_label"]
    assert "fight_preview" in result
    assert result["fight_preview"]["rounds_expected"]


@patch("app.services.ufc_fight_insights.predict_matchup", return_value=None)
@patch("app.services.ufc_fight_insights._build_feature_snapshot", return_value=[])
@patch("app.services.ufc_fight_insights._build_fighter_stats")
@patch("app.services.ufc_fight_insights._pred_row", return_value=None)
@patch("app.services.ufc_fight_insights.build_ufc_daily_board")
@patch("app.services.ufc_fight_insights.get_ufc_fight")
def test_build_ufc_fight_insights_card_fights(
    mock_fight, mock_board, _mock_pred, mock_stats, _mock_feats, _mock_matchup
):
    mock_fight.return_value = {
        "game": SAMPLE_FIGHT,
        "date": "2024-01-13",
        "resolved_date": "2024-01-13",
    }
    mock_board.return_value = {
        "mode": "demo",
        "odds_source": "repository",
        "warnings": [],
        "slate": [
            SAMPLE_BOARD_ROW,
            {
                **SAMPLE_BOARD_ROW,
                "fight_id": "401613931",
                "game_id": "401613931",
                "matchup": "Phil Hawes vs Brunno Ferreira",
            },
        ],
        "top_parlays": [],
    }
    mock_stats.return_value = {"home": {}, "away": {}}

    result = ufi.build_ufc_fight_insights(
        "401613930", game_date=date(2024, 1, 13), use_cache=True
    )
    assert len(result["card_fights"]) == 1
    assert result["card_fights"][0]["fight_id"] == "401613931"


@patch("app.services.ufc_fight_insights.get_ufc_fight", return_value=None)
def test_build_ufc_fight_insights_missing(_mock_fight):
    assert ufi.build_ufc_fight_insights("999", game_date=date(2024, 1, 13)) is None


@patch("app.services.ufc_fight_insights.build_ufc_fight_insights")
def test_ufc_insights_api_not_found(mock_insights):
    mock_insights.return_value = None
    resp = client.get("/api/games/ufc/999/insights?date=2024-01-13")
    assert resp.status_code == 404


@patch("app.services.ufc_fight_insights.build_ufc_fight_insights")
def test_ufc_insights_api_success(mock_insights):
    mock_insights.return_value = {
        "game": SAMPLE_FIGHT,
        "date": "2024-01-13",
        "sport": "ufc",
        "moneyline": {"model_pick": "Magomed Ankalaev"},
        "bets": {"singles": [], "props": []},
        "fighter_stats": {},
        "matchup_board": {"home": {}, "away": {}, "highlights": {}},
        "feature_snapshot": [],
        "card_parlays": [],
        "warnings": [],
        "betting_ready": False,
        "disclaimer": "test",
        "active_model": {},
        "odds_source": "none",
    }
    resp = client.get(
        "/api/games/ufc/401613930/insights?date=2024-01-13&use_cache=true"
    )
    assert resp.status_code == 200
    assert resp.json()["sport"] == "ufc"
    mock_insights.assert_called_once()


def test_ufc_fight_page():
    resp = client.get("/ufc/game/401613930")
    assert resp.status_code == 200
    assert "UFC Fight" in resp.text
    assert "ufc_fight.js" in resp.text
    assert "fighter-stats" in resp.text
    assert "ufc-matchup-insight" in resp.text
    assert "fight-props" in resp.text


@patch("app.services.ufc_fight_insights._load_slate_features", return_value={})
@patch("app.services.ufc_fight_insights._build_fighter_stats")
@patch("app.services.ufc_fight_insights._pred_row", return_value=None)
@patch("app.services.ufc_fight_insights.build_ufc_daily_board")
@patch("app.services.ufc_fight_insights.get_ufc_fight")
@patch("app.services.ufc_fight_insights.predict_matchup")
def test_fight_preview_matches_matchup_probs(
    mock_matchup, mock_fight, mock_board, _mock_pred, mock_stats, _mock_feats
):
    mock_fight.return_value = {
        "game": {
            **SAMPLE_FIGHT,
            "away_team": "Max Holloway",
            "home_team": "Conor McGregor",
        },
        "date": "2026-07-11",
        "resolved_date": "2026-07-11",
    }
    mock_board.return_value = {
        "mode": "demo",
        "odds_source": "repository",
        "warnings": [],
        "slate": [
            {
                **SAMPLE_BOARD_ROW,
                "away_team": "Max Holloway",
                "home_team": "Conor McGregor",
                "model_prob_home": 0.58,
                "model_prob_away": 0.42,
                "model_pick": "Conor McGregor",
                "model_pick_side": "home",
            }
        ],
        "top_parlays": [],
    }
    mock_stats.return_value = {"home": {}, "away": {}}
    mock_matchup.return_value = {
        "predictedWinner": "Max Holloway",
        "predictedWinnerSide": "away",
        "confidence": 60.0,
        "probAway": 0.60,
        "probHome": 0.40,
        "fighterScores": {"fighterA": 61.0, "fighterB": 57.0},
        "categoryBreakdown": {},
        "winMethodProbabilities": {},
        "keyReasons": [],
        "riskFactors": [],
        "modelNotes": [],
    }

    result = ufi.build_ufc_fight_insights(
        "401613930", game_date=date(2026, 7, 11), use_cache=True
    )

    preview = result["fight_preview"]
    ml = result["moneyline"]
    assert preview["pick"] == "Max Holloway"
    assert preview["pick_win_pct"] == 0.60
    assert preview["away_win_pct"] == 0.60
    assert preview["home_win_pct"] == 0.40
    assert ml["model_prob_away"] == 0.60
    assert ml["model_prob_home"] == 0.40
    assert ml["model_pick"] == "Max Holloway"


@patch("app.services.ufc_fight_insights._load_slate_features", return_value={})
@patch("app.services.ufc_fight_insights._build_fighter_stats")
@patch("app.services.ufc_fight_insights._pred_row", return_value=None)
@patch("app.services.ufc_fight_insights.build_ufc_daily_board")
@patch("app.services.ufc_fight_insights.get_ufc_fight")
@patch("app.services.ufc_fight_insights.predict_matchup")
def test_matchup_prediction_full_payload_in_insights(
    mock_matchup, mock_fight, mock_board, _mock_pred, mock_stats, _mock_feats
):
    mock_fight.return_value = {
        "game": SAMPLE_FIGHT,
        "date": "2026-07-11",
        "resolved_date": "2026-07-11",
    }
    mock_board.return_value = {
        "mode": "demo",
        "odds_source": "repository",
        "warnings": [],
        "slate": [SAMPLE_BOARD_ROW],
        "top_parlays": [],
    }
    mock_stats.return_value = {"home": {}, "away": {}}
    mock_matchup.return_value = {
        "predictedWinner": "Magomed Ankalaev",
        "predictedWinnerSide": "home",
        "confidence": 62.0,
        "probAway": 0.38,
        "probHome": 0.62,
        "fighterScores": {"fighterA": 58.0, "fighterB": 61.0},
        "categoryBreakdown": {"striking": "Fighter B edge"},
        "categoryEdges": {"striking": -0.18, "recent_form": 0.05},
        "winMethodProbabilities": {
            "fighterA_KO_TKO": 0.12,
            "fighterB_KO_TKO": 0.22,
            "fighterB_Decision": 0.35,
        },
        "keyReasons": ["Reason one", "Reason two", "Reason three"],
        "riskFactors": ["Risk one", "Risk two"],
        "modelNotes": ["note"],
    }

    result = ufi.build_ufc_fight_insights(
        "401613930", game_date=date(2026, 7, 11), use_cache=True
    )

    mp = result["matchup_prediction"]
    assert mp["keyReasons"] == ["Reason one", "Reason two", "Reason three"]
    assert mp["riskFactors"] == ["Risk one", "Risk two"]
    assert mp["winMethodProbabilities"]["fighterB_Decision"] == 0.35
    assert mp["categoryEdges"]["striking"] == -0.18


@patch("app.services.ufc_fight_insights._load_slate_features", return_value={})
@patch("app.services.ufc_fight_insights._build_fighter_stats")
@patch("app.services.ufc_fight_insights._pred_row", return_value=None)
@patch("app.services.ufc_fight_insights.build_ufc_daily_board")
@patch("app.services.ufc_fight_insights.get_ufc_fight")
@patch("app.services.ufc_fight_insights.predict_matchup")
def test_fight_insights_method_prop_edges(
    mock_matchup, mock_fight, mock_board, _mock_pred, mock_stats, _mock_feats
):
    mock_fight.return_value = {
        "game": SAMPLE_FIGHT,
        "date": "2026-07-11",
        "resolved_date": "2026-07-11",
    }
    board_row = {
        **SAMPLE_BOARD_ROW,
        "method_props": {"fighterA_KO_TKO": 450},
        "totals_line": 2.5,
        "over_odds": -110,
        "under_odds": -110,
    }
    mock_board.return_value = {
        "mode": "demo",
        "odds_source": "repository",
        "warnings": [],
        "slate": [board_row],
        "top_parlays": [],
    }
    mock_stats.return_value = {"home": {}, "away": {}}
    mock_matchup.return_value = {
        "predictedWinner": "Johnny Walker",
        "predictedWinnerSide": "away",
        "confidence": 55.0,
        "probAway": 0.55,
        "probHome": 0.45,
        "winMethodProbabilities": {
            "fighterA_KO_TKO": 0.30,
            "fighterA_Submission": 0.05,
            "fighterA_Decision": 0.20,
            "fighterB_KO_TKO": 0.18,
            "fighterB_Submission": 0.07,
            "fighterB_Decision": 0.20,
        },
        "keyReasons": [],
        "riskFactors": [],
    }

    result = ufi.build_ufc_fight_insights(
        "401613930", game_date=date(2026, 7, 11), use_cache=True
    )

    props = result["bets"]["props"]
    method_props = [p for p in props if p.get("market") == "method"]
    assert method_props
    assert method_props[0]["method_key"] == "fighterA_KO_TKO"
    assert method_props[0]["plus_ev"] is True
    assert method_props[0]["edge"] >= 0.08


@patch("app.services.ufc_fight_insights.enrich_fight_media")
@patch("app.services.ufc_fight_insights.build_ufc_daily_board")
@patch("app.services.ufc_fight_insights.get_ufc_fight")
def test_ufc_insights_api_json_safe(mock_fight, mock_board, mock_enrich):
    import math

    mock_fight.return_value = {
        "game": SAMPLE_FIGHT,
        "date": "2024-01-13",
        "resolved_date": "2024-01-13",
    }
    mock_enrich.return_value = SAMPLE_FIGHT
    mock_board.return_value = {
        "odds_source": "none",
        "warnings": [],
        "slate": [
            {
                **SAMPLE_BOARD_ROW,
                "totals_line": float("nan"),
                "over_odds": float("nan"),
                "under_odds": float("nan"),
            }
        ],
        "top_parlays": [],
        "active_moneyline_model": {},
    }
    resp = client.get(
        "/api/games/ufc/401613930/insights?date=2024-01-13&use_cache=true"
    )
    assert resp.status_code == 200
    body = resp.json()
    props = body.get("bets", {}).get("props") or []
    assert props == [] or not any(
        isinstance(p.get("line"), float) and math.isnan(p["line"]) for p in props
    )
