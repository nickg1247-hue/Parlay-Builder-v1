"""Tests for MLB game page model explanations."""

from datetime import date
from unittest.mock import patch

from app.services.mlb_game_explanations import build_mlb_game_explanation

SAMPLE_FEATS = {
    "home_team": "New York Yankees",
    "away_team": "Boston Red Sox",
    "home_pitcher_era": 3.2,
    "away_pitcher_era": 4.5,
    "home_pitcher_era_l5": 2.8,
    "away_pitcher_era_l5": 5.1,
    "home_last10_win_pct": 0.65,
    "away_last10_win_pct": 0.45,
    "home_last10_run_diff": 1.2,
    "away_last10_run_diff": -0.4,
    "home_season_win_pct": 0.58,
    "away_season_win_pct": 0.52,
    "home_home_split_win_pct": 0.62,
    "away_away_split_win_pct": 0.48,
    "home_bullpen_era_14d": 3.8,
    "away_bullpen_era_14d": 4.6,
    "home_rest_days": 1,
    "away_rest_days": 1,
    "park_factor_runs": 1.08,
    "home_season_run_diff": 0.4,
    "away_season_run_diff": 0.2,
    "elo_home_pre": 1540,
    "elo_away_pre": 1490,
}

SAMPLE_BOARD_ROW = {
    "home_team": "New York Yankees",
    "away_team": "Boston Red Sox",
    "model_prob_home": 0.61,
    "model_pick_side": "home",
    "model_pick_team": "New York Yankees",
    "model_pick_prob": 0.61,
    "model_confidence": "High",
    "expected_total_runs": 9.1,
    "ou_line": 8.5,
    "totals_pick": "Over",
    "model_prob_over": 0.56,
    "market_prob_over": 0.5,
}


@patch("app.services.mlb_game_explanations.feature_row_for_game")
def test_build_explanation_includes_both_sides(mock_feats):
    mock_feats.return_value = SAMPLE_FEATS
    out = build_mlb_game_explanation(
        "777001",
        date(2025, 8, 15),
        SAMPLE_BOARD_ROW,
        use_cache=True,
    )
    assert out is not None
    assert out["model_pick_team"] == "New York Yankees"
    assert len(out["why_home"]) >= 2
    assert out["totals"]["pick"] == "Over"
    assert any("park" in b.lower() for b in out["totals"]["bullets"])
    l10 = next(r for r in out["factor_comparison"] if "10 win" in r["factor"])
    assert "New York Yankees" in l10["detail"]
    assert "Boston Red Sox" in l10["detail"]
    assert l10["edge"] == "home"
    factors = {row["factor"] for row in out["factor_comparison"]}
    assert "Starting pitcher ERA (season)" in factors


@patch("app.services.mlb_game_explanations.feature_row_for_game")
def test_build_explanation_away_factors_when_away_better(mock_feats):
    feats = {
        **SAMPLE_FEATS,
        "home_pitcher_era": 5.0,
        "away_pitcher_era": 3.1,
        "home_last10_win_pct": 0.4,
        "away_last10_win_pct": 0.7,
        "elo_home_pre": 1460,
        "elo_away_pre": 1550,
    }
    board = {
        **SAMPLE_BOARD_ROW,
        "model_prob_home": 0.38,
        "model_pick_side": "away",
        "model_pick_team": "Boston Red Sox",
        "model_pick_prob": 0.62,
    }
    mock_feats.return_value = feats
    out = build_mlb_game_explanation("777001", date(2025, 8, 15), board, use_cache=True)
    assert out["why_away"]
    assert out["model_pick_team"] == "Boston Red Sox"
