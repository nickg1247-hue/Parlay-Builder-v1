"""Tests for UFC market evaluation."""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pandas as pd

from app.odds.ufc_market_eval import _merge_fights_odds, run_market_evaluation


def test_merge_fights_odds_normalizes_names():
    fights = pd.DataFrame(
        [
            {
                "date": "2024-01-13",
                "home_team": "Magomed Ankalaev",
                "away_team": "Johnny Walker",
                "home_win": 1,
                "season": 2024,
            }
        ]
    )
    odds = pd.DataFrame(
        [
            {
                "date": "2024-01-13",
                "home_team": "Magomed Ankalaev",
                "away_team": "Johnny Walker",
                "home_ml": -180,
                "away_ml": 155,
            }
        ]
    )
    merged = _merge_fights_odds(fights, odds)
    assert len(merged) == 1
    assert merged.iloc[0].home_ml == -180


@patch("app.odds.ufc_market_eval._write_outputs")
@patch("app.odds.ufc_market_eval.load_holdout_odds")
@patch("app.odds.ufc_market_eval.build_features_for_history")
@patch("app.odds.ufc_market_eval.load_fights")
@patch("app.odds.ufc_market_eval.load_model_artifact")
def test_run_market_evaluation_no_odds(
    mock_artifact, mock_games, mock_feat, mock_odds, mock_write, tmp_path, monkeypatch
):
    mock_artifact.return_value = {"model_version": "ufc_test"}
    mock_games.return_value = pd.DataFrame(
        {
            "date": ["2024-01-13"],
            "season": [2024],
            "home_team": ["A"],
            "away_team": ["B"],
            "home_win": [1],
        }
    )
    mock_feat.return_value = mock_games.return_value.copy()
    mock_odds.return_value = pd.DataFrame()
    monkeypatch.setattr(
        "app.odds.ufc_market_eval.MARKET_EVAL_JSON",
        tmp_path / "ufc_market_metrics.json",
    )

    results = run_market_evaluation()
    assert results["status"] == "no_odds"
    assert results["matched_games"] == 0
    mock_write.assert_called_once()


@patch("app.odds.ufc_market_eval._write_outputs")
@patch("app.odds.ufc_market_eval.predict_home_win_proba", return_value=[0.6])
@patch("app.odds.ufc_market_eval.load_holdout_odds")
@patch("app.odds.ufc_market_eval.build_features_for_history")
@patch("app.odds.ufc_market_eval.load_fights")
@patch("app.odds.ufc_market_eval.load_model_artifact")
def test_run_market_evaluation_with_odds(
    mock_artifact,
    mock_games,
    mock_feat,
    mock_odds,
    _mock_pred,
    mock_write,
):
    mock_artifact.return_value = {"model_version": "ufc_test"}
    fights = pd.DataFrame(
        {
            "fight_id": ["1", "2"],
            "date": ["2024-01-13", "2024-01-13"],
            "season": [2024, 2024],
            "home_team": ["Magomed Ankalaev", "Phil Hawes"],
            "away_team": ["Johnny Walker", "Brunno Ferreira"],
            "home_win": [1, 0],
        }
    )
    mock_games.return_value = fights
    mock_feat.return_value = fights.copy()
    mock_odds.return_value = pd.DataFrame(
        {
            "date": ["2024-01-13", "2024-01-13"],
            "home_team": ["Magomed Ankalaev", "Phil Hawes"],
            "away_team": ["Johnny Walker", "Brunno Ferreira"],
            "home_ml": [-180, 110],
            "away_ml": [155, -130],
        }
    )
    _mock_pred.return_value = np.array([0.6, 0.45])

    results = run_market_evaluation(edge_threshold=0.08)
    assert results["matched_games"] == 2
    assert results["log_loss_model"] is not None
    assert results["log_loss_market"] is not None
    mock_write.assert_called_once()
