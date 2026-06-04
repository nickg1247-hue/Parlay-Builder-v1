"""Optional live MLB odds via The Odds API free tier."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
SPORT_MLB = "baseball_mlb"
MARKET_H2H = "h2h"
MARKET_TOTALS = "totals"
# One request with both markets still costs 1 credit (per The Odds API pricing).
MARKETS_H2H_AND_TOTALS = f"{MARKET_H2H},{MARKET_TOTALS}"


def _fetch_odds(
    api_key: str,
    markets: str,
    regions: str = "us",
) -> list[dict[str, Any]] | None:
    url = f"{ODDS_API_BASE}/sports/{SPORT_MLB}/odds"
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


def fetch_mlb_odds(
    api_key: str | None = None,
    regions: str = "us",
    include_totals: bool = True,
) -> list[dict[str, Any]] | None:
    """
    Fetch MLB odds (1 request ≈ 1 credit).

    With include_totals=True uses markets=h2h,totals in a single call.
    """
    key = api_key or os.getenv("ODDS_API_KEY", "").strip()
    if not key:
        logger.info("ODDS_API_KEY not set — skipping live odds fetch")
        return None

    markets = MARKETS_H2H_AND_TOTALS if include_totals else MARKET_H2H
    try:
        return _fetch_odds(key, markets, regions)
    except Exception as exc:
        logger.warning("The Odds API request failed: %s", exc)
        return None


def fetch_mlb_moneylines(
    api_key: str | None = None,
    regions: str = "us",
) -> list[dict[str, Any]] | None:
    """Backward-compatible: h2h + totals in one request."""
    return fetch_mlb_odds(api_key=api_key, regions=regions, include_totals=True)
