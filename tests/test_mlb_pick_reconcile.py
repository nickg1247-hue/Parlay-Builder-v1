"""Tests for moneyline pick reconciliation."""

import pytest

from app.services.mlb_game_explanations import build_mlb_factor_comparison
from app.services.mlb_pick_reconcile import reconcile_model_prob_home


def _yankees_at_reds_feats() -> dict:
    """Synthetic Reds (home) vs Yankees where away dominates visible factors."""
    return {
        "home_pitcher_era": 4.8,
        "away_pitcher_era": 3.2,
        "home_pitcher_era_l5": 5.1,
        "away_pitcher_era_l5": 2.9,
        "home_last10_win_pct": 0.42,
        "away_last10_win_pct": 0.68,
        "home_last10_run_diff": -0.8,
        "away_last10_run_diff": 1.2,
        "home_season_win_pct": 0.44,
        "away_season_win_pct": 0.58,
        "home_home_split_win_pct": 0.48,
        "away_away_split_win_pct": 0.55,
        "home_bullpen_era_14d": 4.5,
        "away_bullpen_era_14d": 3.4,
        "elo_home_pre": 1490,
        "elo_away_pre": 1565,
        "home_rest_days": 1,
        "away_rest_days": 1,
    }


def test_factor_comparison_favors_yankees_over_reds():
    feats = _yankees_at_reds_feats()
    factors = build_mlb_factor_comparison(feats, "Cincinnati Reds", "New York Yankees")
    votes = {"home": 0, "away": 0, "neutral": 0}
    for f in factors:
        votes[f["edge"]] += 1
    assert votes["away"] >= votes["home"] + 3
    assert votes["home"] <= 2


def test_reconcile_flips_weak_home_pick_when_factors_favor_away():
    feats = _yankees_at_reds_feats()
    factors = build_mlb_factor_comparison(feats, "Cincinnati Reds", "New York Yankees")
    rec = reconcile_model_prob_home(
        0.53,
        factors,
        feats["elo_home_pre"],
        feats["elo_away_pre"],
    )
    assert rec.adjusted is True
    assert rec.prob_home < 0.5
    assert rec.factor_majority_side == "away"
    assert rec.raw_prob_home == pytest.approx(0.53)


def test_reconcile_leaves_strong_agreeing_pick():
    feats = _yankees_at_reds_feats()
    factors = build_mlb_factor_comparison(feats, "Cincinnati Reds", "New York Yankees")
    rec = reconcile_model_prob_home(
        0.38,
        factors,
        feats["elo_home_pre"],
        feats["elo_away_pre"],
    )
    assert rec.adjusted is False
    assert rec.prob_home == pytest.approx(0.38)


def test_reconcile_respects_small_factor_gap():
    factors = [
        {"edge": "away"},
        {"edge": "home"},
        {"edge": "away"},
    ]
    rec = reconcile_model_prob_home(0.53, factors, 1500, 1500)
    assert rec.adjusted is False
    assert abs(rec.prob_home - 0.53) < 1e-6


def test_reconcile_uses_market_when_it_agrees_with_factors():
    feats = _yankees_at_reds_feats()
    factors = build_mlb_factor_comparison(feats, "Cincinnati Reds", "New York Yankees")
    rec_no_market = reconcile_model_prob_home(
        0.53,
        factors,
        feats["elo_home_pre"],
        feats["elo_away_pre"],
    )
    rec_market = reconcile_model_prob_home(
        0.53,
        factors,
        feats["elo_home_pre"],
        feats["elo_away_pre"],
        market_home=0.35,
    )
    assert rec_market.adjusted is True
    assert rec_market.prob_home < 0.5
    assert rec_market.prob_home <= rec_no_market.prob_home
