"""NFL player-prop market keys, labels, and scoring distribution families."""

from __future__ import annotations

MARKET_LABELS: dict[str, str] = {
    "player_pass_yds": "Passing yards",
    "player_pass_tds": "Passing TDs",
    "player_pass_attempts": "Pass attempts",
    "player_pass_completions": "Pass completions",
    "player_pass_interceptions": "Interceptions",
    "player_pass_longest_completion": "Longest completion",
    "player_rush_yds": "Rushing yards",
    "player_rush_attempts": "Rush attempts",
    "player_rush_longest": "Longest rush",
    "player_receptions": "Receptions",
    "player_reception_yds": "Receiving yards",
    "player_reception_longest": "Longest reception",
    "player_rush_reception_yds": "Rush + rec yards",
    "player_anytime_td": "Anytime TD",
}

# ESPN box/gamelog stat keys used for each Odds API market.
MARKET_STAT: dict[str, str] = {
    "player_pass_yds": "passingYards",
    "player_pass_tds": "passingTouchdowns",
    "player_pass_attempts": "passingAttempts",
    "player_pass_completions": "passingCompletions",
    "player_pass_interceptions": "interceptions",
    "player_pass_longest_completion": "passingLong",
    "player_rush_yds": "rushingYards",
    "player_rush_attempts": "rushingAttempts",
    "player_rush_longest": "rushingLong",
    "player_receptions": "receptions",
    "player_reception_yds": "receivingYards",
    "player_reception_longest": "receivingLong",
    "player_rush_reception_yds": "rushRecYards",
    "player_anytime_td": "anytimeTd",
}

YARDS_MARKETS = frozenset(
    {
        "player_pass_yds",
        "player_rush_yds",
        "player_reception_yds",
        "player_rush_reception_yds",
        "player_pass_longest_completion",
        "player_rush_longest",
        "player_reception_longest",
    }
)
COUNT_MARKETS = frozenset(
    {
        "player_pass_tds",
        "player_pass_attempts",
        "player_pass_completions",
        "player_pass_interceptions",
        "player_rush_attempts",
        "player_receptions",
        "player_anytime_td",
    }
)
QB_MARKETS = frozenset(
    {
        "player_pass_yds",
        "player_pass_tds",
        "player_pass_attempts",
        "player_pass_completions",
        "player_pass_interceptions",
        "player_pass_longest_completion",
    }
)
RB_MARKETS = frozenset(
    {
        "player_rush_yds",
        "player_rush_attempts",
        "player_rush_longest",
        "player_rush_reception_yds",
    }
)
PASS_CATCHER_MARKETS = frozenset(
    {
        "player_receptions",
        "player_reception_yds",
        "player_reception_longest",
        "player_rush_reception_yds",
    }
)

POSITION_GROUPS = {
    "QB": "QB",
    "RB": "RB",
    "FB": "RB",
    "WR": "WR",
    "TE": "TE",
}

VERY_STRONG_SCORE = 78.0
ELITE_SCORE = 88.0
MIN_PROP_SCORE = 62.0
MIN_EDGE = 0.05
VERY_STRONG_EDGE = 0.07
ELITE_EDGE = 0.11


def market_label(market_type: str) -> str:
    return MARKET_LABELS.get(market_type, market_type.replace("_", " ").title())


def list_nfl_market_types() -> list[dict[str, str]]:
    return [{"key": key, "label": label} for key, label in MARKET_LABELS.items()]


def canonical_market_type(market_key: str) -> tuple[str, str]:
    if market_key.endswith("_alternate"):
        return market_key[: -len("_alternate")], "alternate"
    return market_key, "main"


def position_group(position: str | None) -> str:
    raw = str(position or "").strip().upper()
    return POSITION_GROUPS.get(raw, raw or "")


def uses_normal_distribution(market_type: str) -> bool:
    return market_type in YARDS_MARKETS
