"""Attach O/U lines to slate from Odds API or historical cache."""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from app.odds.mlb_odds_free import TOTALS_2025_CSV
from app.odds.team_aliases import is_valid_american_odds, normalize_team_name
from app.odds.the_odds_api import fetch_mlb_odds


def _parse_totals_from_events(events: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for event in events:
        home = normalize_team_name(event.get("home_team", ""))
        away = normalize_team_name(event.get("away_team", ""))
        commence = event.get("commence_time", "")[:10]
        lines: list[float] = []
        over_prices: list[int] = []
        under_prices: list[int] = []
        for book in event.get("bookmakers", []):
            for market in book.get("markets", []):
                if market.get("key") != "totals":
                    continue
                over_point = None
                over_price = None
                under_price = None
                for outcome in market.get("outcomes", []):
                    name = (outcome.get("name") or "").lower()
                    if name == "over":
                        over_point = outcome.get("point")
                        over_price = outcome.get("price")
                    elif name == "under":
                        under_price = outcome.get("price")
                if (
                    over_point is not None
                    and over_price is not None
                    and under_price is not None
                ):
                    if is_valid_american_odds(over_price) and is_valid_american_odds(
                        under_price
                    ):
                        lines.append(float(over_point))
                        over_prices.append(int(over_price))
                        under_prices.append(int(under_price))
        if not lines:
            continue
        rows.append(
            {
                "date": commence,
                "home_team": home,
                "away_team": away,
                "ou_line": float(pd.Series(lines).median()),
                "over_odds": int(pd.Series(over_prices).median()),
                "under_odds": int(pd.Series(under_prices).median()),
            }
        )
    return pd.DataFrame(rows)


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
) -> pd.DataFrame:
    odds_df = pd.DataFrame()
    if not use_cache:
        events = fetch_mlb_odds(include_totals=True)
        if events:
            odds_df = _parse_totals_from_events(events)

    if odds_df.empty and (use_cache or TOTALS_2025_CSV.exists()):
        odds_df = _load_cached_totals(game_date)

    if odds_df.empty:
        return slate

    out = slate.copy()
    out["date_key"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    odds_df = odds_df.copy()
    odds_df["home_team"] = odds_df["home_team"].map(normalize_team_name)
    odds_df["away_team"] = odds_df["away_team"].map(normalize_team_name)
    odds_df["date_key"] = pd.to_datetime(odds_df["date"]).dt.strftime("%Y-%m-%d")
    return out.merge(
        odds_df,
        on=["date_key", "home_team", "away_team"],
        how="left",
        suffixes=("", "_totals"),
    )
