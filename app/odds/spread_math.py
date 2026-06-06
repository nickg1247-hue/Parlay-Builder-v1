"""MLB run line (spread) cover grading and market implied probabilities."""

from __future__ import annotations

import math

from app.odds.odds_math import american_to_implied_prob, remove_vig


def norm_cdf(x: float, mean: float = 0.0, std: float = 1.0) -> float:
    if std <= 0:
        return 1.0 if x >= mean else 0.0
    z = (x - mean) / std
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def side_covers(
    side: str,
    home_score: int | float,
    away_score: int | float,
    home_spread_point: float,
    away_spread_point: float,
) -> bool:
    """
    Team covers when team_score + spread_point > opponent_score.
    MLB run line: home -1.5 needs win by 2+; away +1.5 covers losses by 1.
    """
    home = float(home_score)
    away = float(away_score)
    if side == "home":
        return home + float(home_spread_point) > away
    if side == "away":
        return away + float(away_spread_point) > home
    raise ValueError(f"side must be 'home' or 'away', got {side!r}")


def market_probs_from_american_spread(
    home_point: float,
    home_price: int,
    away_point: float,
    away_price: int,
) -> tuple[float, float]:
    """Vig-free implied cover probabilities from run line American prices."""
    raw_home = american_to_implied_prob(home_price)
    raw_away = american_to_implied_prob(away_price)
    return remove_vig(raw_home, raw_away)


def model_prob_home_cover(
    predicted_margin: float,
    margin_std: float,
    home_spread_point: float,
) -> float:
    """P(home covers at book line): margin > -home_spread_point."""
    cutoff = -float(home_spread_point)
    return 1.0 - norm_cdf(cutoff, predicted_margin, margin_std)


def model_prob_away_cover(
    predicted_margin: float,
    margin_std: float,
    away_spread_point: float,
) -> float:
    """P(away covers at book line): margin < away_spread_point."""
    cutoff = float(away_spread_point)
    return norm_cdf(cutoff, predicted_margin, margin_std)
