"""American odds ↔ implied probability and vig removal."""

from __future__ import annotations


def american_to_implied_prob(american_odds: int | float) -> float:
    """Convert American moneyline to implied probability (with vig)."""
    odds = float(american_odds)
    if odds == 0:
        raise ValueError("American odds cannot be zero")
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return abs(odds) / (abs(odds) + 100.0)


def remove_vig(implied_home: float, implied_away: float) -> tuple[float, float]:
    """Normalize two-way implied probs so they sum to 1."""
    total = implied_home + implied_away
    if total <= 0:
        raise ValueError("Implied probabilities must be positive")
    return implied_home / total, implied_away / total


def market_probs_from_american(home_ml: int, away_ml: int) -> tuple[float, float]:
    """Raw implied then vig-free home/away win probabilities."""
    raw_home = american_to_implied_prob(home_ml)
    raw_away = american_to_implied_prob(away_ml)
    return remove_vig(raw_home, raw_away)


def american_payout_profit(american_odds: int | float, won: bool) -> float:
    """Flat $1 stake profit (negative if loss)."""
    if not won:
        return -1.0
    odds = float(american_odds)
    if odds > 0:
        return odds / 100.0
    return 100.0 / abs(odds)
