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


def american_to_decimal(american_odds: int | float) -> float:
    """American moneyline to decimal payout multiplier (stake + profit per $1)."""
    odds = float(american_odds)
    if odds > 0:
        return 1.0 + odds / 100.0
    return 1.0 + 100.0 / abs(odds)


def parlay_decimal_payout(american_odds_list: list[int | float]) -> float:
    """Product of leg decimal odds for a parlay."""
    payout = 1.0
    for odds in american_odds_list:
        payout *= american_to_decimal(odds)
    return payout


def joint_probability(leg_probs: list[float]) -> float:
    """Independence assumption: product of leg win probabilities."""
    prob = 1.0
    for p in leg_probs:
        prob *= p
    return prob


def parlay_ev(model_joint_prob: float, decimal_payout: float) -> float:
    """EV per $1 staked: (joint_prob × payout) - 1."""
    return model_joint_prob * decimal_payout - 1.0


def american_payout_profit(american_odds: int | float, won: bool) -> float:
    """Flat $1 stake profit (negative if loss)."""
    if not won:
        return -1.0
    odds = float(american_odds)
    if odds > 0:
        return odds / 100.0
    return 100.0 / abs(odds)
