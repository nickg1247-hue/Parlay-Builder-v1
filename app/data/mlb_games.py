"""Shared MLB games loader with derived total_runs."""

from __future__ import annotations

import pandas as pd

from app.models.mlb_baseline import load_games


def ensure_total_runs(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "total_runs" not in out.columns:
        out["total_runs"] = out["home_score"] + out["away_score"]
    return out


def load_games_with_totals() -> pd.DataFrame:
    return ensure_total_runs(load_games())
