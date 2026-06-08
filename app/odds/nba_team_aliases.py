"""Normalize NBA team names to canonical full names (aligned with ESPN displayName)."""

from __future__ import annotations

# Keys are lowercased lookup strings; values match ESPN scoreboard displayName where possible.
_NBA_ALIASES: dict[str, str] = {
    "la clippers": "LA Clippers",
    "los angeles clippers": "LA Clippers",
    "la lakers": "Los Angeles Lakers",
    "los angeles lakers": "Los Angeles Lakers",
    "ny knicks": "New York Knicks",
    "new york knicks": "New York Knicks",
    "ny nets": "Brooklyn Nets",
    "brooklyn nets": "Brooklyn Nets",
    "gs warriors": "Golden State Warriors",
    "golden state warriors": "Golden State Warriors",
    "sa spurs": "San Antonio Spurs",
    "san antonio spurs": "San Antonio Spurs",
    "no pelicans": "New Orleans Pelicans",
    "new orleans pelicans": "New Orleans Pelicans",
    "oklahoma city thunder": "Oklahoma City Thunder",
    "portland trail blazers": "Portland Trail Blazers",
    "philadelphia 76ers": "Philadelphia 76ers",
    "phila 76ers": "Philadelphia 76ers",
}


def normalize_nba_team_name(name: str) -> str:
    if not name or not str(name).strip():
        return name
    raw = " ".join(str(name).strip().split())
    key = raw.lower()
    if key in _NBA_ALIASES:
        return _NBA_ALIASES[key]
    return raw
