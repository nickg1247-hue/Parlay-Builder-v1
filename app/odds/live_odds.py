"""Whether to call The Odds API for live sportsbook lines."""

from __future__ import annotations

import os


def live_odds_enabled() -> bool:
    """
    Live sportsbook odds are opt-in.

    Default (USE_LIVE_ODDS unset or false): model-only + free MLB data — no API credits.
    Set USE_LIVE_ODDS=true and ODDS_API_KEY in .env to enable live moneyline/O/U/spread.
    """
    flag = os.getenv("USE_LIVE_ODDS", "false").strip().lower()
    if flag not in ("1", "true", "yes", "on"):
        return False
    return bool(os.getenv("ODDS_API_KEY", "").strip())
