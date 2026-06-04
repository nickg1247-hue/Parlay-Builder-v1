"""Optional live MLB moneylines via The Odds API free tier."""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
SPORT_MLB = "baseball_mlb"
MARKET_H2H = "h2h"


def fetch_mlb_moneylines(
    api_key: str | None = None,
    regions: str = "us",
) -> list[dict[str, Any]] | None:
    """
    Fetch current MLB h2h odds (1 request ≈ 1 credit).

    Returns None if no API key (graceful skip).
    """
    key = api_key or os.getenv("ODDS_API_KEY", "").strip()
    if not key:
        logger.info("ODDS_API_KEY not set — skipping live odds fetch")
        return None

    url = f"{ODDS_API_BASE}/sports/{SPORT_MLB}/odds"
    params = {
        "apiKey": key,
        "regions": regions,
        "markets": MARKET_H2H,
        "oddsFormat": "american",
    }
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            return response.json()
    except Exception as exc:
        logger.warning("The Odds API request failed: %s", exc)
        return None
