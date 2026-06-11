"""Tests for user-weighted NBA custom model."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from app.features.nba_custom_factors import build_factor_breakdown, compute_factor_edges
from app.models.nba_custom import load_custom_weights, predict_custom_home_proba

PARQUET = Path(__file__).resolve().parents[1] / "data" / "processed" / "nba_games.parquet"

BASE_FEAT = {
    "home_team": "Boston Celtics",
    "away_team": "New York Knicks",
    "home_last10_win_pct": 0.5,
    "away_last10_win_pct": 0.5,
    "home_season_pts_for": 110,
    "away_season_pts_for": 110,
    "home_season_pts_against": 110,
    "away_season_pts_against": 110,
    "home_last10_pts_for": 110,
    "away_last10_pts_for": 110,
    "home_last10_pts_against": 110,
    "away_last10_pts_against": 110,
    "home_rest_days": 2,
    "away_rest_days": 2,
    "home_b2b": 0,
    "away_b2b": 0,
}


def test_load_custom_weights_sums_to_one():
    cfg = load_custom_weights()
    total = sum(cfg["factors"].values())
    assert abs(total - 1.0) < 1e-6
    assert len(cfg["factors"]) == 16


def test_home_court_biases_toward_home():
    cfg = load_custom_weights()
    edges = compute_factor_edges(BASE_FEAT)
    assert edges["home_court_advantage"] == 1.0
    bd = build_factor_breakdown(BASE_FEAT, cfg["factors"], score_scale=cfg["score_scale"])
    assert bd["model_prob_home"] > 0.5


def test_strong_home_offense_increases_home_prob():
    cfg = load_custom_weights()
    weak = build_factor_breakdown(BASE_FEAT, cfg["factors"], score_scale=cfg["score_scale"])
    strong = build_factor_breakdown(
        {**BASE_FEAT, "home_season_pts_for": 125, "home_last10_pts_for": 125},
        cfg["factors"],
        score_scale=cfg["score_scale"],
    )
    assert strong["model_prob_home"] > weak["model_prob_home"]


@pytest.mark.skipif(not PARQUET.exists(), reason="nba_games.parquet not present")
def test_predict_custom_on_real_slate_if_data_exists():
    slate = pd.DataFrame(
        [
            {
                "game_id": "test1",
                "date": "2026-04-10",
                "season": 2026,
                "home_team": "Boston Celtics",
                "away_team": "New York Knicks",
            }
        ]
    )
    probs = predict_custom_home_proba(slate, date(2026, 4, 10))
    assert len(probs) == 1
    assert 0.0 < float(probs[0]) < 1.0
