"""Attach live Odds API lines to a CFB slate dataframe."""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from app.odds.cfb_odds_repository import get_cfb_odds_for_date
from app.odds.cfb_team_aliases import normalize_team_name


def attach_cfb_odds(
    slate_df: pd.DataFrame,
    game_date: date,
    *,
    force_refresh: bool = False,
) -> tuple[pd.DataFrame, str]:
    """Merge quota-gated live odds onto slate rows by normalized team matchup."""
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
        merged[col] = np.nan

    odds_games, source = get_cfb_odds_for_date(
        game_date,
        force_refresh=force_refresh,
        include_spreads=True,
        include_totals=True,
    )
    if not odds_games:
        return merged, "none"

    odds_by_matchup: dict[tuple[str, str], dict[str, Any]] = {}
    for og in odds_games:
        key = (
            normalize_team_name(og.get("home_team", "")),
            normalize_team_name(og.get("away_team", "")),
        )
        odds_by_matchup[key] = og

    for idx, row in merged.iterrows():
        key = (
            normalize_team_name(row["home_team"]),
            normalize_team_name(row["away_team"]),
        )
        match = odds_by_matchup.get(key)
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
