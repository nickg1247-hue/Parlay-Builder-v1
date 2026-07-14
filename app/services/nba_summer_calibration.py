"""Post-hoc adjustments for NBA Summer League predictions (no separate retrain)."""

from __future__ import annotations

import math
import os
from typing import Any

import numpy as np
import pandas as pd

from app.models.nba_totals import DEFAULT_TOTAL_STD, prob_over_normal


def summer_ml_shrink() -> float:
    """How much of franchise model signal to keep (0–1). Rest shrinks toward 0.5."""
    try:
        return float(os.getenv("NBA_SUMMER_ML_SHRINK", "0.55"))
    except ValueError:
        return 0.55


def summer_totals_pace_mult() -> float:
    """Inflate expected total points vs regular-season model (summer pace is higher)."""
    try:
        return float(os.getenv("NBA_SUMMER_TOTALS_PACE_MULT", "1.08"))
    except ValueError:
        return 1.08


def summer_margin_dampen() -> float:
    """Pull predicted margin toward 0 (higher upset rate / short minutes)."""
    try:
        return float(os.getenv("NBA_SUMMER_MARGIN_DAMPEN", "0.65"))
    except ValueError:
        return 0.65


def summer_margin_std_mult() -> float:
    """Widen margin uncertainty when recomputing cover probs."""
    try:
        return float(os.getenv("NBA_SUMMER_MARGIN_STD_MULT", "1.35"))
    except ValueError:
        return 1.35


def summer_home_court_edge() -> float:
    """Home-court factor edge for summer (regular season uses +1.0). Neutral sites ~0.25."""
    try:
        return float(os.getenv("NBA_SUMMER_HOME_COURT_EDGE", "0.25"))
    except ValueError:
        return 0.25


def is_summer_row(row: Any) -> bool:
    if isinstance(row, dict):
        if row.get("is_summer") or row.get("league_tag") == "summer":
            return True
        return bool(row.get("summer_league"))
    if row.get("is_summer") if hasattr(row, "get") else False:
        return True
    tag = getattr(row, "league_tag", None)
    if tag == "summer":
        return True
    return bool(getattr(row, "summer_league", None) or getattr(row, "is_summer", False))


def shrink_home_prob(prob: float, *, kappa: float | None = None) -> float:
    k = summer_ml_shrink() if kappa is None else kappa
    k = max(0.0, min(1.0, float(k)))
    p = float(prob)
    return float(np.clip(0.5 + k * (p - 0.5), 1e-4, 1.0 - 1e-4))


def apply_summer_calibration(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adjust model outputs for Summer League rows in place.

    - Shrink moneyline toward 50/50 (franchise form ≠ summer roster)
    - Inflate expected totals (faster pace)
    - Dampen margins + widen cover uncertainty
    """
    if df.empty:
        return df
    out = df.copy()
    if "is_summer" not in out.columns and "summer_league" not in out.columns:
        return out

    summer_mask = out.apply(is_summer_row, axis=1)
    if not summer_mask.any():
        return out

    kappa = summer_ml_shrink()
    pace = summer_totals_pace_mult()
    damp = summer_margin_dampen()
    std_mult = summer_margin_std_mult()

    if "model_prob_home" in out.columns:
        # Dedicated summer_model already carry-calibrated — don't double-shrink.
        shrink_mask = summer_mask.copy()
        if "pick_source" in out.columns:
            shrink_mask = shrink_mask & (out["pick_source"].fillna("") != "summer_model")
        if shrink_mask.any():
            probs = out.loc[shrink_mask, "model_prob_home"].astype(float)
            out.loc[shrink_mask, "model_prob_home"] = probs.map(
                lambda p: shrink_home_prob(p, kappa=kappa)
            )
            out["model_prob_away"] = 1.0 - out["model_prob_home"]

    if "ml_prob_home" in out.columns:
        shrink_mask = summer_mask.copy()
        if "pick_source" in out.columns:
            shrink_mask = shrink_mask & (out["pick_source"].fillna("") != "summer_model")
        if shrink_mask.any():
            ml = out.loc[shrink_mask, "ml_prob_home"]
            out.loc[shrink_mask, "ml_prob_home"] = ml.map(
                lambda p: shrink_home_prob(float(p), kappa=kappa) if pd.notna(p) else p
            )

    if "model_margin" in out.columns:
        margins = out.loc[summer_mask, "model_margin"].astype(float)
        out.loc[summer_mask, "model_margin"] = margins * damp

        # Recompute cover probs with wider std when spread points exist.
        try:
            from app.models.nba_margin import (
                load_margin_artifact,
                model_prob_away_cover,
                model_prob_home_cover,
            )

            artifact = load_margin_artifact()
            base_std = float(artifact.get("margin_std", 12.0)) * std_mult
            for idx in out.index[summer_mask]:
                margin = out.at[idx, "model_margin"]
                if margin is None or (isinstance(margin, float) and math.isnan(margin)):
                    continue
                hp = out.at[idx, "home_spread_point"] if "home_spread_point" in out.columns else None
                ap = out.at[idx, "away_spread_point"] if "away_spread_point" in out.columns else None
                if hp is not None and not (isinstance(hp, float) and math.isnan(hp)):
                    out.at[idx, "model_prob_home_cover"] = round(
                        model_prob_home_cover(float(margin), base_std, float(hp)), 4
                    )
                if ap is not None and not (isinstance(ap, float) and math.isnan(ap)):
                    out.at[idx, "model_prob_away_cover"] = round(
                        model_prob_away_cover(float(margin), base_std, float(ap)), 4
                    )
        except (FileNotFoundError, ImportError, ValueError, KeyError):
            pass

    if "expected_total_pts" in out.columns:
        totals = out.loc[summer_mask, "expected_total_pts"].astype(float) * pace
        out.loc[summer_mask, "expected_total_pts"] = totals
        if "ou_line" in out.columns:
            try:
                from app.models.nba_totals import load_totals_artifact

                std = float(load_totals_artifact().get("total_std", DEFAULT_TOTAL_STD))
            except (FileNotFoundError, ImportError, ValueError, KeyError):
                std = DEFAULT_TOTAL_STD
            for idx in out.index[summer_mask]:
                exp = out.at[idx, "expected_total_pts"]
                line = out.at[idx, "ou_line"]
                if exp is None or line is None:
                    continue
                if isinstance(exp, float) and math.isnan(exp):
                    continue
                if isinstance(line, float) and math.isnan(line):
                    continue
                out.at[idx, "model_prob_over"] = round(
                    prob_over_normal(float(exp), std, float(line)), 4
                )

    # Preserve summer_model source; only mark franchise-adjusted rows.
    if "pick_source" in out.columns:
        calibrated = summer_mask & (out["pick_source"].fillna("") != "summer_model")
        out.loc[calibrated, "pick_source"] = "summer_calibrated"
        missing = summer_mask & out["pick_source"].isna()
        out.loc[missing, "pick_source"] = "summer_calibrated"
    else:
        out.loc[summer_mask, "pick_source"] = "summer_calibrated"
    return out


def summer_prediction_disclaimer() -> str:
    return (
        "Summer League moneylines use a historical summer Elo + franchise-prior model "
        "(public ESPN results; no Odds API). Backtested selective leans "
        "(|model − 50%| ≥ calibrated edge) hit ~62% on 2025 holdout. "
        "Spreads/totals still use adjusted season models — research only, not bankroll advice."
    )
