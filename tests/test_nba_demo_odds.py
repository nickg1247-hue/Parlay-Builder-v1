"""Tests for NBA demo benchmark odds."""

from __future__ import annotations

import pandas as pd

from app.odds.nba_demo_odds import (
    LEAGUE_AVG_TOTAL,
    LEAGUE_HOME_WIN_P,
    apply_demo_benchmark_odds,
    prob_to_american,
)


def test_benchmark_independent_of_model():
    df = pd.DataFrame(
        [
            {"model_prob_home": 0.9, "model_margin": 15.0, "expected_total_pts": 240.0},
            {"model_prob_home": 0.2, "model_margin": -12.0, "expected_total_pts": 210.0},
        ]
    )
    out = apply_demo_benchmark_odds(df)
    assert out.loc[0, "home_ml"] == out.loc[1, "home_ml"]
    assert out.loc[0, "ou_line"] == LEAGUE_AVG_TOTAL
    assert out.loc[0, "home_spread_point"] == -5.5
    market_p = LEAGUE_HOME_WIN_P
    assert abs(float(df.loc[0, "model_prob_home"]) - market_p) > 0.1


def test_prob_to_american():
    assert prob_to_american(LEAGUE_HOME_WIN_P) < 0
