"""Attach live Odds API (or ESPN ingest) lines to an NFL slate dataframe."""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from app.odds.nfl_odds_repository import get_nfl_odds_for_date
from app.odds.nfl_team_aliases import normalize_nfl_team


def attach_nfl_odds(
    slate_df: pd.DataFrame,
    game_date: date,
    *,
    force_refresh: bool = False,
) -> tuple[pd.DataFrame, str]:
    merged = slate_df.copy()
    for col in (
        "home_ml",
        "away_ml",
        "home_spread_point",
        "home_spread_american",
        "away_spread_point",
        "away_spread_american",
        "ou_line",
        "over_odds",
        "under_odds",
    ):
        if col not in merged.columns:
            merged[col] = np.nan

    odds_games, source = get_nfl_odds_for_date(game_date, force_refresh=force_refresh)
    if odds_games:
        by_matchup = {
            (
                normalize_nfl_team(og.get("home_team", "")),
                normalize_nfl_team(og.get("away_team", "")),
            ): og
            for og in odds_games
        }
        for idx, row in merged.iterrows():
            key = (
                normalize_nfl_team(row.get("home_team_abbr") or row.get("home_team")),
                normalize_nfl_team(row.get("away_team_abbr") or row.get("away_team")),
            )
            match = by_matchup.get(key)
            if not match:
                continue
            for col in (
                "home_ml",
                "away_ml",
                "home_spread_point",
                "home_spread_american",
                "away_spread_point",
                "away_spread_american",
                "ou_line",
                "over_odds",
                "under_odds",
            ):
                val = match.get(col)
                if val is not None:
                    merged.at[idx, col] = val
        return merged, source

    # Fallback: ESPN lines captured on ingest / live scoreboard.
    espn_used = False
    for idx, row in merged.iterrows():
        if pd.notna(row.get("home_ml")) and pd.notna(row.get("away_ml")):
            continue
        if pd.notna(row.get("espn_home_ml")) and pd.notna(row.get("espn_away_ml")):
            merged.at[idx, "home_ml"] = row["espn_home_ml"]
            merged.at[idx, "away_ml"] = row["espn_away_ml"]
            espn_used = True
        if pd.isna(row.get("home_spread_point")) and pd.notna(row.get("espn_spread")):
            merged.at[idx, "home_spread_point"] = row["espn_spread"]
            espn_used = True
        if pd.isna(row.get("ou_line")) and pd.notna(row.get("espn_ou")):
            merged.at[idx, "ou_line"] = row["espn_ou"]
            espn_used = True
    return merged, "espn_scoreboard" if espn_used else "none"
