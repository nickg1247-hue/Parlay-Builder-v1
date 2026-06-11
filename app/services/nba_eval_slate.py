"""Attach final scores to demo/eval slates for holdout dates."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from app.models.nba_baseline import load_games
from app.odds.nba_team_aliases import normalize_nba_team_name


def attach_actual_results(merged: pd.DataFrame, game_date: date) -> pd.DataFrame:
    """Add actual_margin, actual_total_pts, actual_home_win when game is final."""
    try:
        history = load_games()
    except FileNotFoundError:
        return merged

    iso = game_date.isoformat()
    hist = history.copy()
    hist["date"] = pd.to_datetime(hist["date"]).dt.strftime("%Y-%m-%d")
    day = hist[
        (hist["date"] == iso)
        & hist["home_score"].notna()
        & hist["away_score"].notna()
    ]
    if day.empty:
        return merged

    by_matchup: dict[tuple[str, str], pd.Series] = {}
    for _, row in day.iterrows():
        key = (
            normalize_nba_team_name(row["home_team"]),
            normalize_nba_team_name(row["away_team"]),
        )
        by_matchup[key] = row

    out = merged.copy()
    out["actual_home_win"] = np.nan
    out["actual_margin"] = np.nan
    out["actual_total_pts"] = np.nan

    for idx, row in out.iterrows():
        key = (
            normalize_nba_team_name(row["home_team"]),
            normalize_nba_team_name(row["away_team"]),
        )
        match = by_matchup.get(key)
        if match is None:
            continue
        hs = float(match["home_score"])
        aws = float(match["away_score"])
        out.at[idx, "actual_home_win"] = int(match["home_win"])
        out.at[idx, "actual_margin"] = round(hs - aws, 1)
        out.at[idx, "actual_total_pts"] = round(hs + aws, 1)

    return out


def eval_row_fields(row) -> dict:
    """Model vs actual for completed holdout games."""
    empty = {
        "actual_home_win": None,
        "actual_margin": None,
        "actual_total_pts": None,
        "model_ml_correct": None,
        "actual_went_over": None,
        "model_ou_correct": None,
    }
    ah = getattr(row, "actual_home_win", None)
    if ah is None or (isinstance(ah, float) and np.isnan(ah)):
        return empty

    model_home = getattr(row, "model_prob_home", None)
    ml_correct = None
    if model_home is not None and not (isinstance(model_home, float) and np.isnan(model_home)):
        ml_correct = int(float(model_home) >= 0.5) == int(ah)

    line = getattr(row, "ou_line", None)
    actual_total = getattr(row, "actual_total_pts", None)
    went_over = ou_correct = None
    if (
        line is not None
        and actual_total is not None
        and not pd.isna(line)
        and not pd.isna(actual_total)
    ):
        if float(line) % 1 == 0.5:
            went_over = int(float(actual_total) > float(line))
        else:
            went_over = int(float(actual_total) >= float(line))
        model_over = getattr(row, "model_prob_over", None)
        if model_over is not None and not (isinstance(model_over, float) and np.isnan(model_over)):
            pick_over = float(model_over) >= 0.5
            ou_correct = int(pick_over) == went_over

    return {
        "actual_home_win": int(ah),
        "actual_margin": getattr(row, "actual_margin", None),
        "actual_total_pts": getattr(row, "actual_total_pts", None),
        "model_ml_correct": ml_correct,
        "actual_went_over": went_over,
        "model_ou_correct": ou_correct,
    }
