"""Tests for MLB moneyline ensemble helpers."""

import numpy as np
import pandas as pd
import pytest

from app.models.mlb_ensemble import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MODERATE,
    CONFIDENCE_NO_PICK,
    ENSEMBLE_WEIGHT_ELO,
    ENSEMBLE_WEIGHT_GBC,
    ENSEMBLE_WEIGHT_LOGISTIC,
    _blend_probs,
    accuracy_by_confidence_bucket,
    confidence_tier,
    model_pick_from_prob,
    promotion_gate_improves,
)
from app.services.daily_board import _slate_rows


def test_confidence_tier_thresholds():
    assert confidence_tier(CONFIDENCE_NO_PICK - 0.01) == "Lean only"
    assert confidence_tier(CONFIDENCE_NO_PICK + 0.01) == "Low"
    assert confidence_tier(CONFIDENCE_LOW + 0.01) == "Moderate"
    assert confidence_tier(CONFIDENCE_MODERATE + 0.01) == "High"
    assert confidence_tier(CONFIDENCE_HIGH + 0.01) == "Very high"


def test_blend_probs_weights():
    logistic = np.array([0.6])
    gbc = np.array([0.7])
    elo = np.array([0.5])
    blended = _blend_probs(logistic, gbc, elo)
    expected = (
        ENSEMBLE_WEIGHT_LOGISTIC * 0.6
        + ENSEMBLE_WEIGHT_GBC * 0.7
        + ENSEMBLE_WEIGHT_ELO * 0.5
    )
    assert blended[0] == pytest.approx(expected, rel=1e-6)


def test_model_pick_lean_only_below_54():
    pick = model_pick_from_prob(0.52, "Yankees", "Red Sox")
    assert pick.model_pick_side == "home"
    assert pick.model_pick_action == "lean_only"
    assert pick.model_confidence == "Lean only"


def test_model_pick_high_confidence():
    pick = model_pick_from_prob(0.35, "Yankees", "Red Sox")
    assert pick.model_pick_side == "away"
    assert pick.model_pick_prob == pytest.approx(0.65, rel=1e-4)
    assert pick.model_pick_action == "pick"
    assert pick.model_confidence == "High"


def test_model_pick_blocked_when_stale():
    pick = model_pick_from_prob(0.68, "Yankees", "Red Sox", block_strong_picks=True)
    assert pick.model_pick_action == "lean_only"
    assert pick.model_confidence == "Blocked (stale data)"


def test_promotion_gate_requires_two_improvements():
    baseline = {
        "log_loss": 0.68,
        "brier": 0.24,
        "high_confidence_accuracy": 0.62,
        "plus_ev_roi": 0.01,
        "mean_clv": 0.0,
    }
    better = {
        "log_loss": 0.67,
        "brier": 0.23,
        "high_confidence_accuracy": 0.63,
        "plus_ev_roi": 0.01,
        "mean_clv": 0.0,
    }
    ok, improved = promotion_gate_improves(baseline, better)
    assert ok is True
    assert "log_loss" in improved
    assert "brier" in improved

    worse = {
        **baseline,
        "log_loss": 0.69,
        "brier": 0.241,
        "high_confidence_accuracy": 0.61,
    }
    ok2, improved2 = promotion_gate_improves(baseline, worse)
    assert ok2 is False
    assert len(improved2) < 2


def test_accuracy_by_confidence_bucket_tracks_no_pick_pct():
    y = np.array([1, 0, 1, 0])
    probs = np.array([0.51, 0.49, 0.66, 0.34])
    buckets = accuracy_by_confidence_bucket(y, probs)
    assert buckets["summary"]["total_games"] == 4
    assert buckets["no_pick"]["n"] == 2
    assert buckets["summary"]["no_pick_pct"] == pytest.approx(0.5)


def test_slate_rows_exposes_confidence_fields():
    merged = pd.DataFrame(
        [
            {
                "game_id": "1",
                "date": "2025-08-15",
                "home_team": "Detroit Tigers",
                "away_team": "Boston Red Sox",
                "model_prob_home": 0.64,
                "model_prob_away": 0.36,
                "home_ml": -120,
                "away_ml": 110,
            }
        ]
    )
    rows = _slate_rows(merged, has_odds=True, totals_by_game={}, min_edge=0.08)
    row = rows[0]
    assert row["model_pick_action"] == "pick"
    assert row["model_confidence"] == "High"
    assert row["model_confidence_prob"] == pytest.approx(0.64, rel=1e-3)
