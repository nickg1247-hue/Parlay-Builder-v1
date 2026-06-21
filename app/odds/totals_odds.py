"""Attach O/U lines to slate from odds repository or historical CSV."""

from __future__ import annotations

from datetime import date

import pandas as pd

from app.odds.mlb_odds_free import TOTALS_2025_CSV
from app.odds.live_odds import live_odds_enabled
from app.odds.odds_repository import (
    games_to_totals_dataframe,
    get_mlb_odds_for_date,
    has_date,
)
from app.odds.team_aliases import normalize_team_name


def _load_cached_totals(game_date: date) -> pd.DataFrame:
    if not TOTALS_2025_CSV.exists():
        return pd.DataFrame()
    odds = pd.read_csv(TOTALS_2025_CSV)
    odds["date"] = pd.to_datetime(odds["date"]).dt.strftime("%Y-%m-%d")
    return odds[odds["date"] == game_date.isoformat()].copy()


def attach_totals_odds(
    slate: pd.DataFrame,
    game_date: date,
    use_cache: bool = False,
    force_refresh: bool = False,
) -> pd.DataFrame:
    odds_df = pd.DataFrame()

    if use_cache:
        if has_date(game_date):
            games, _ = get_mlb_odds_for_date(game_date)
            if games:
                odds_df = games_to_totals_dataframe(games, game_date)
        if odds_df.empty and TOTALS_2025_CSV.exists():
            odds_df = _load_cached_totals(game_date)
    elif live_odds_enabled():
        games, _ = get_mlb_odds_for_date(
            game_date,
            force_refresh=force_refresh,
            include_totals=True,
            include_spreads=False,
        )
        if games:
            odds_df = games_to_totals_dataframe(games, game_date)
    elif has_date(game_date):
        games, _ = get_mlb_odds_for_date(game_date)
        if games:
            odds_df = games_to_totals_dataframe(games, game_date)

    if odds_df.empty:
        return slate

    out = slate.copy()
    out["date_key"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    odds_df = odds_df.copy()
    odds_df["home_team"] = odds_df["home_team"].map(normalize_team_name)
    odds_df["away_team"] = odds_df["away_team"].map(normalize_team_name)
    odds_df["date_key"] = pd.to_datetime(odds_df["date"]).dt.strftime("%Y-%m-%d")
    odds_df = odds_df.drop_duplicates(
        subset=["date_key", "home_team", "away_team"], keep="first"
    )
    return out.merge(
        odds_df,
        on=["date_key", "home_team", "away_team"],
        how="left",
        suffixes=("", "_totals"),
    )
