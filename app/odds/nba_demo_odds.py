"""Independent benchmark lines for NBA demo — NOT derived from model outputs."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

# Naive market priors (independent of per-game model). See DEV.md / TOTALS_NBA.md.
DEMO_BOARD_DATE = "2026-04-10"
LEAGUE_HOME_WIN_P = 0.54
LEAGUE_AVG_TOTAL = 224.5
BENCHMARK_HOME_SPREAD = -5.5
DEFAULT_SPREAD_AMERICAN = -110
DEFAULT_TOTALS_AMERICAN = -110


def prob_to_american(prob: float) -> int:
    p = min(max(float(prob), 0.05), 0.95)
    if p >= 0.5:
        return int(round(-100.0 * p / (1.0 - p)))
    return int(round(100.0 * (1.0 - p) / p))


def apply_demo_benchmark_odds(df: pd.DataFrame) -> pd.DataFrame:
    """
    Fixed league-average benchmark sportsbook lines for demo edge math.

    Intentionally independent of model_prob_home / model_margin / expected_total_pts
    so Model P ≠ Market P and spread/O/U edges are meaningful in demo.
    """
    out = df.copy()
    home_ml = prob_to_american(LEAGUE_HOME_WIN_P)
    away_ml = prob_to_american(1.0 - LEAGUE_HOME_WIN_P)
    away_spread = -BENCHMARK_HOME_SPREAD

    for idx in out.index:
        out.at[idx, "home_ml"] = home_ml
        out.at[idx, "away_ml"] = away_ml
        out.at[idx, "home_spread_point"] = BENCHMARK_HOME_SPREAD
        out.at[idx, "away_spread_point"] = away_spread
        out.at[idx, "home_spread_american"] = DEFAULT_SPREAD_AMERICAN
        out.at[idx, "away_spread_american"] = DEFAULT_SPREAD_AMERICAN
        out.at[idx, "ou_line"] = LEAGUE_AVG_TOTAL
        out.at[idx, "over_odds"] = DEFAULT_TOTALS_AMERICAN
        out.at[idx, "under_odds"] = DEFAULT_TOTALS_AMERICAN

    return out


def slate_has_real_odds(df: pd.DataFrame) -> bool:
    if df.empty or "home_ml" not in df.columns:
        return False
    return bool(df["home_ml"].notna().any())
