"""Live totals scoring for daily slate."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from app.features.mlb_totals_pregame import build_totals_features_for_slate
from app.models.mlb_totals import (
    load_totals_artifact,
    predict_expected_total_runs,
    predict_prob_over,
    prob_over_poisson,
    score_totals_pick,
)
from app.odds.odds_math import market_probs_from_american_totals
from app.odds.team_aliases import is_valid_american_odds
from app.odds.totals_odds import attach_totals_odds
from app.parlay.slate import build_slate_dataframe, build_slate_from_history


def build_totals_slate(
    game_date: date,
    use_cache: bool = False,
    moneyline_slate: pd.DataFrame | None = None,
) -> pd.DataFrame:
    ml = moneyline_slate
    if ml is None:
        ml = (
            build_slate_from_history(game_date)
            if use_cache
            else build_slate_dataframe(game_date)
        )
    if ml.empty:
        return pd.DataFrame()

    base = ml[["game_id", "date", "home_team", "away_team"]].copy()
    if "season" in ml.columns:
        base["season"] = ml["season"]
    else:
        base["season"] = pd.to_datetime(base["date"]).dt.year
    for col in ("home_starting_pitcher", "away_starting_pitcher"):
        if col in ml.columns:
            base[col] = ml[col]

    artifact = load_totals_artifact()
    featured = build_totals_features_for_slate(
        base,
        era_medians=artifact["era_medians"],
        rest_fill=artifact["rest_fill"],
    )
    featured["expected_total_runs"] = predict_expected_total_runs(featured)

    merged = attach_totals_odds(featured, game_date, use_cache=use_cache)
    model_over_arr: np.ndarray | None = None
    if "ou_line" in merged.columns and merged["ou_line"].notna().any():
        lines = merged["ou_line"].astype(float).values
        model_over_arr = predict_prob_over(featured, lines)

    rows: list[dict] = []
    for i, row in enumerate(merged.itertuples(index=False)):
        ou = getattr(row, "ou_line", None)
        market_over = None
        if pd.notna(ou) and pd.notna(getattr(row, "over_odds", None)):
            if is_valid_american_odds(row.over_odds) and is_valid_american_odds(
                row.under_odds
            ):
                market_over, _ = market_probs_from_american_totals(
                    int(row.over_odds), int(row.under_odds)
                )
        model_over = (
            float(model_over_arr[i])
            if model_over_arr is not None and pd.notna(ou)
            else None
        )
        ou_val = float(ou) if pd.notna(ou) else None
        scored = score_totals_pick(
            float(row.expected_total_runs),
            ou_val,
            model_over,
            market_over,
        )
        if scored.get("ou_line") is not None and pd.isna(scored["ou_line"]):
            scored["ou_line"] = None
        rows.append(
            {
                "game_id": str(row.game_id),
                "date": row.date,
                "home_team": row.home_team,
                "away_team": row.away_team,
                "matchup": f"{row.away_team} @ {row.home_team}",
                **scored,
                "over_odds": int(row.over_odds)
                if pd.notna(getattr(row, "over_odds", None))
                else None,
                "under_odds": int(row.under_odds)
                if pd.notna(getattr(row, "under_odds", None))
                else None,
            }
        )
    return pd.DataFrame(rows)
