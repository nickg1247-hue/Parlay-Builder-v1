"""Attach live Odds API moneylines to a UFC slate dataframe."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from app.odds.ufc_fighter_aliases import fighter_match_key, fighters_match, normalize_fighter_name
from app.odds.ufc_odds_free import load_holdout_odds
from app.odds.ufc_odds_repository import get_ufc_odds_for_date


def attach_ufc_odds(
    slate_df: pd.DataFrame,
    card_date: date,
    *,
    force_refresh: bool = False,
) -> tuple[pd.DataFrame, str]:
    merged = slate_df.copy()
    merged["home_ml"] = np.nan
    merged["away_ml"] = np.nan
    merged["totals_line"] = np.nan
    merged["over_odds"] = np.nan
    merged["under_odds"] = np.nan
    merged["method_props"] = None
    merged["goes_distance_yes"] = np.nan
    merged["goes_distance_no"] = np.nan

    odds_fights, source = get_ufc_odds_for_date(card_date, force_refresh=force_refresh)
    if not odds_fights and card_date < date.today():
        holdout = load_holdout_odds({card_date.isoformat()})
        if not holdout.empty:
            odds_fights = holdout.to_dict(orient="records")
            source = str(holdout.iloc[0].get("odds_source", "holdout_csv"))
    if not odds_fights:
        return merged, "none"

    odds_by_key: dict[tuple[str, str], dict] = {}
    for row in odds_fights:
        key = (
            fighter_match_key(row.get("home_team", "")),
            fighter_match_key(row.get("away_team", "")),
        )
        odds_by_key[key] = row
        odds_by_key[(key[1], key[0])] = {**row, "_swapped": True}

    for idx, slate_row in merged.iterrows():
        home = normalize_fighter_name(slate_row["home_team"])
        away = normalize_fighter_name(slate_row["away_team"])
        key = (fighter_match_key(home), fighter_match_key(away))
        match = odds_by_key.get(key)
        if not match:
            for odds_row in odds_fights:
                if fighters_match(home, odds_row.get("home_team", "")) and fighters_match(
                    away, odds_row.get("away_team", "")
                ):
                    match = odds_row
                    break
                if fighters_match(home, odds_row.get("away_team", "")) and fighters_match(
                    away, odds_row.get("home_team", "")
                ):
                    match = {**odds_row, "_swapped": True}
                    break
        if not match:
            continue
        if match.get("_swapped"):
            merged.at[idx, "home_ml"] = match.get("away_ml")
            merged.at[idx, "away_ml"] = match.get("home_ml")
        else:
            merged.at[idx, "home_ml"] = match.get("home_ml")
            merged.at[idx, "away_ml"] = match.get("away_ml")
        if match.get("totals_line") is not None:
            merged.at[idx, "totals_line"] = match.get("totals_line")
        if match.get("over_odds") is not None:
            merged.at[idx, "over_odds"] = match.get("over_odds")
        if match.get("under_odds") is not None:
            merged.at[idx, "under_odds"] = match.get("under_odds")
        if match.get("method_props"):
            merged.at[idx, "method_props"] = match.get("method_props")
        if match.get("goes_distance_yes") is not None:
            merged.at[idx, "goes_distance_yes"] = match.get("goes_distance_yes")
        if match.get("goes_distance_no") is not None:
            merged.at[idx, "goes_distance_no"] = match.get("goes_distance_no")

    return merged, source
