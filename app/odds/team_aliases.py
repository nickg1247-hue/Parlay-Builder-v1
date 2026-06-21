"""Normalize team names between odds sources and mlb_games (MLB Stats API names)."""

from __future__ import annotations

# Keys are lowercased lookup strings; values are canonical MLB Stats API names.
_ALIASES: dict[str, str] = {
    "oakland athletics": "Athletics",
    "athletics": "Athletics",
    "arizona d-backs": "Arizona Diamondbacks",
    "d-backs": "Arizona Diamondbacks",
    "la angels": "Los Angeles Angels",
    "la dodgers": "Los Angeles Dodgers",
    "ny yankees": "New York Yankees",
    "ny mets": "New York Mets",
    "st. louis cardinals": "St. Louis Cardinals",
    "st louis cardinals": "St. Louis Cardinals",
    "tampa bay": "Tampa Bay Rays",
    "washington": "Washington Nationals",
}


def normalize_team_name(name: str) -> str:
    if not name or not str(name).strip():
        return name
    raw = " ".join(str(name).strip().split())
    key = raw.lower()
    if key in _ALIASES:
        return _ALIASES[key]
    # Fix duplicated tokens from source data (e.g. "Athletics Athletics")
    parts = raw.split()
    if len(parts) >= 2 and len(parts) % 2 == 0:
        half = len(parts) // 2
        if parts[:half] == parts[half:]:
            raw = " ".join(parts[:half])
            key = raw.lower()
            if key in _ALIASES:
                return _ALIASES[key]
    return raw


def is_valid_american_odds(odds: int | float | None) -> bool:
    """Reject placeholders and non–moneyline values from scraped feeds."""
    if odds is None:
        return False
    try:
        if isinstance(odds, float) and odds != odds:  # NaN
            return False
        value = int(odds)
    except (TypeError, ValueError):
        return False
    if value == 0:
        return False
    return 100 <= abs(value) <= 500
