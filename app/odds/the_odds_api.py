"""Optional live MLB odds via The Odds API (low-level HTTP; persistence in odds_repository)."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from app.odds.live_odds import live_odds_enabled

logger = logging.getLogger(__name__)

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
SPORT_MLB = "baseball_mlb"
SPORT_NBA = "basketball_nba"
MARKET_H2H = "h2h"
MARKET_TOTALS = "totals"
MARKET_SPREADS = "spreads"
MARKETS_H2H_AND_TOTALS = f"{MARKET_H2H},{MARKET_TOTALS}"
MARKETS_H2H_TOTALS_SPREADS = f"{MARKET_H2H},{MARKET_TOTALS},{MARKET_SPREADS}"


def clear_odds_cache() -> None:
    """No-op: in-memory cache replaced by odds_repository (kept for test compat)."""


def _markets_string(include_totals: bool, include_spreads: bool) -> str:
    if include_spreads and include_totals:
        return MARKETS_H2H_TOTALS_SPREADS
    if include_totals:
        return MARKETS_H2H_AND_TOTALS
    if include_spreads:
        return f"{MARKET_H2H},{MARKET_SPREADS}"
    return MARKET_H2H


def _api_key(explicit: str | None = None) -> str | None:
    key = explicit or os.getenv("ODDS_API_KEY", "").strip()
    return key or None


def _fetch_live_odds(
    api_key: str,
    markets: str,
    regions: str = "us",
    *,
    sport: str = SPORT_MLB,
) -> list[dict[str, Any]]:
    url = f"{ODDS_API_BASE}/sports/{sport}/odds"
    params = {
        "apiKey": api_key,
        "regions": regions,
        "markets": markets,
        "oddsFormat": "american",
    }
    with httpx.Client(timeout=30.0) as client:
        response = client.get(url, params=params)
        response.raise_for_status()
        return response.json()


def _fetch_historical_odds(
    api_key: str,
    snapshot_date: str,
    markets: str,
    regions: str = "us",
) -> list[dict[str, Any]]:
    """
    Historical snapshot: closest odds at or before snapshot_date.

    GET /v4/historical/sports/baseball_mlb/odds
    Docs: https://the-odds-api.com/liveapi/guides/v4/#get-historical-odds
    """
    url = f"{ODDS_API_BASE}/historical/sports/{SPORT_MLB}/odds"
    params = {
        "apiKey": api_key,
        "regions": regions,
        "markets": markets,
        "oddsFormat": "american",
        "dateFormat": "iso",
        "date": snapshot_date,
    }
    with httpx.Client(timeout=30.0) as client:
        response = client.get(url, params=params)
        response.raise_for_status()
        body = response.json()
        return body.get("data") or []


def fetch_live_mlb_odds(
    api_key: str | None = None,
    regions: str = "us",
    include_totals: bool = True,
    include_spreads: bool = False,
) -> list[dict[str, Any]] | None:
    """Live odds for today/upcoming — one request ≈ 1 credit."""
    if not live_odds_enabled() and api_key is None:
        return None
    key = _api_key(api_key)
    if not key:
        return None
    markets = _markets_string(include_totals, include_spreads)
    return _fetch_live_odds(key, markets, regions)


def fetch_live_nba_odds(
    api_key: str | None = None,
    regions: str = "us",
    include_spreads: bool = False,
) -> list[dict[str, Any]] | None:
    """Live NBA odds — one request ≈ 1 credit (h2h or h2h+spreads). No historical endpoint."""
    if not live_odds_enabled() and api_key is None:
        return None
    key = _api_key(api_key)
    if not key:
        return None
    markets = (
        f"{MARKET_H2H},{MARKET_SPREADS}" if include_spreads else MARKET_H2H
    )
    return _fetch_live_odds(key, markets, regions, sport=SPORT_NBA)


def fetch_historical_mlb_odds(
    snapshot_date: str,
    api_key: str | None = None,
    regions: str = "us",
    include_totals: bool = True,
    include_spreads: bool = False,
) -> list[dict[str, Any]] | None:
    """Historical odds snapshot — paid plan; cost ≈ 10 × markets × regions."""
    if not live_odds_enabled() and api_key is None:
        return None
    key = _api_key(api_key)
    if not key:
        return None
    markets = _markets_string(include_totals, include_spreads)
    return _fetch_historical_odds(key, snapshot_date, markets, regions)


def fetch_mlb_odds(
    api_key: str | None = None,
    regions: str = "us",
    include_totals: bool = True,
    include_spreads: bool = False,
    force_refresh: bool = False,
    bypass_min_ttl: bool = False,
) -> list[dict[str, Any]] | None:
    """
    Backward-compatible wrapper: today's odds via persistent repository.

    Returns raw API-shaped events reconstructed from the repository snapshot.
    Prefer get_mlb_odds_for_date() for new code.
    """
    from datetime import date

    from app.odds.odds_repository import get_mlb_odds_for_date

    games, _ = get_mlb_odds_for_date(
        date.today(),
        force_refresh=force_refresh,
        include_totals=include_totals,
        include_spreads=include_spreads,
        bypass_min_ttl=bypass_min_ttl,
    )
    if not games:
        return None
    return _games_to_events(games)


def fetch_mlb_moneylines(
    api_key: str | None = None,
    regions: str = "us",
    force_refresh: bool = False,
) -> list[dict[str, Any]] | None:
    """Backward-compatible: h2h + totals in one repository-backed request."""
    return fetch_mlb_odds(
        api_key=api_key,
        regions=regions,
        include_totals=True,
        force_refresh=force_refresh,
    )


def _games_to_events(games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Minimal event-shaped dicts for legacy parsers."""
    events: list[dict[str, Any]] = []
    for g in games:
        bookmakers: list[dict[str, Any]] = []
        markets: list[dict[str, Any]] = []
        if g.get("home_ml") is not None and g.get("away_ml") is not None:
            markets.append(
                {
                    "key": "h2h",
                    "outcomes": [
                        {"name": g["home_team"], "price": g["home_ml"]},
                        {"name": g["away_team"], "price": g["away_ml"]},
                    ],
                }
            )
        if g.get("ou_line") is not None:
            markets.append(
                {
                    "key": "totals",
                    "outcomes": [
                        {
                            "name": "Over",
                            "price": g.get("over_odds"),
                            "point": g.get("ou_line"),
                        },
                        {"name": "Under", "price": g.get("under_odds")},
                    ],
                }
            )
        if g.get("home_spread_point") is not None or g.get("away_spread_point") is not None:
            spread_outcomes = []
            if g.get("away_spread_point") is not None:
                spread_outcomes.append(
                    {
                        "name": g["away_team"],
                        "price": g.get("away_spread_american"),
                        "point": g.get("away_spread_point"),
                    }
                )
            if g.get("home_spread_point") is not None:
                spread_outcomes.append(
                    {
                        "name": g["home_team"],
                        "price": g.get("home_spread_american"),
                        "point": g.get("home_spread_point"),
                    }
                )
            if spread_outcomes:
                markets.append({"key": "spreads", "outcomes": spread_outcomes})
        if markets:
            bookmakers.append({"markets": markets})
        events.append(
            {
                "home_team": g["home_team"],
                "away_team": g["away_team"],
                "commence_time": g.get("commence_time"),
                "bookmakers": bookmakers,
            }
        )
    return events

