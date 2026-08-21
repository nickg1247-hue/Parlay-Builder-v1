"""Shared sportsbook keys for player props (MLB and NFL)."""

from __future__ import annotations

from typing import Any

DEFAULT_PROP_BOOKMAKER = "consensus"
DEFAULT_DISPLAY_BOOKMAKER = "draftkings"


def normalize_prop_sport(sport: str | None) -> str:
    raw = str(sport or "mlb").strip().lower()
    if raw in ("nfl", "football", "americanfootball_nfl"):
        return "nfl"
    if raw in ("mlb", "baseball", "baseball_mlb", ""):
        return "mlb"
    return "mlb"

CONSENSUS_PROP_BOOKS = frozenset(
    {
        "draftkings",
        "fanduel",
        "betmgm",
        "betrivers",
        "williamhill_us",
        "bovada",
        "betonlineag",
        "espnbet",
        "fanatics",
    }
)

PROP_BOOKMAKERS: dict[str, str] = {
    "consensus": "Best line (full markets only)",
    "draftkings": "DraftKings",
    "fanduel": "FanDuel",
    "betmgm": "BetMGM",
    "betrivers": "BetRivers",
    "williamhill_us": "Caesars",
    "bovada": "Bovada",
    "betonlineag": "BetOnline",
    "espnbet": "theScore Bet",
    "fanatics": "Fanatics",
}

BOOKMAKER_ALIASES: dict[str, str] = {
    "caesars": "williamhill_us",
    "williamhill": "williamhill_us",
    "thescore": "espnbet",
    "pointsbetus": DEFAULT_PROP_BOOKMAKER,
    "pointsbet": DEFAULT_PROP_BOOKMAKER,
}


def normalize_prop_bookmaker(raw: str | None) -> str:
    key = (raw or DEFAULT_DISPLAY_BOOKMAKER).strip().lower()
    key = BOOKMAKER_ALIASES.get(key, key)
    if key in PROP_BOOKMAKERS:
        return key
    return DEFAULT_DISPLAY_BOOKMAKER


def bookmaker_label(book: str) -> str:
    return PROP_BOOKMAKERS.get(book, book)


def list_static_prop_bookmakers() -> list[dict[str, Any]]:
    return [
        {
            "key": key,
            "label": label,
            "has_cache": key == DEFAULT_PROP_BOOKMAKER,
            "has_props": True,
        }
        for key, label in PROP_BOOKMAKERS.items()
    ]
