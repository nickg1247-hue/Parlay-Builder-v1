"""NFL-specific projections and over/under probabilities.

Opportunity × efficiency × game environment. Recent role is weighted above
season averages. Hit rates are supporting context only.
"""

from __future__ import annotations

import math
import statistics
from typing import Any

from app.odds.odds_math import american_to_implied_prob, market_probs_from_american_totals
from app.services.prop_engine.nfl_markets import (
    COUNT_MARKETS,
    ELITE_EDGE,
    ELITE_SCORE,
    MIN_EDGE,
    MIN_PROP_SCORE,
    VERY_STRONG_EDGE,
    VERY_STRONG_SCORE,
    uses_normal_distribution,
)
from app.services.prop_engine.probabilities import model_probabilities
from app.services.prop_engine.utils import recent_game_window

YES_NO_LINE = 0.5


def _norm_cdf(x: float, mu: float, sigma: float) -> float:
    if sigma <= 0:
        return 1.0 if x >= mu else 0.0
    return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2.0))))


def _weighted_mean(values: list[float], weights: list[float]) -> float:
    if not values:
        return 0.0
    pairs = list(zip(values, weights))
    total_w = sum(w for _, w in pairs)
    if total_w <= 0:
        return statistics.mean(values)
    return sum(v * w for v, w in pairs) / total_w


def _script_multiplier(
    *,
    market_type: str,
    team_spread: float | None,
    team_implied_total: float | None,
    league_implied_total: float = 22.5,
) -> float:
    """Scale expected opportunity from spread / implied total. No invented usage."""
    mult = 1.0
    if team_implied_total is not None and league_implied_total > 0:
        volume = team_implied_total / league_implied_total
        volume = max(0.82, min(1.18, volume))
        if market_type.startswith("player_pass_") or market_type in (
            "player_receptions",
            "player_reception_yds",
            "player_reception_longest",
        ):
            mult *= 0.55 + 0.45 * volume
        elif market_type.startswith("player_rush_"):
            mult *= 0.70 + 0.30 * volume
        else:
            mult *= 0.65 + 0.35 * volume
    if team_spread is None:
        return round(mult, 4)
    # Negative spread = favorite. Favorites run more; underdogs throw more.
    if team_spread <= -6.5:
        if market_type.startswith("player_rush_"):
            mult *= 1.06
        elif market_type.startswith("player_pass_") or market_type in (
            "player_receptions",
            "player_reception_yds",
        ):
            mult *= 0.96
    elif team_spread >= 6.5:
        if market_type.startswith("player_pass_") or market_type in (
            "player_receptions",
            "player_reception_yds",
        ):
            mult *= 1.06
        elif market_type.startswith("player_rush_"):
            mult *= 0.94
    return round(max(0.75, min(1.25, mult)), 4)


def build_nfl_projection(
    values: list[float],
    *,
    market_type: str,
    team_spread: float | None = None,
    team_implied_total: float | None = None,
    injury_note: str | None = None,
) -> dict[str, Any]:
    """Recent-weighted projection. L3 outranks season average when roles change."""
    clean = [float(v) for v in values if v is not None]
    if not clean:
        return {
            "model_projection": None,
            "median_outcome": None,
            "std_dev": None,
            "sample_games": 0,
            "projection_confidence": "low",
            "l3_avg": None,
            "season_avg": None,
            "role_shift": None,
            "env_multiplier": 1.0,
            "injury_note": injury_note,
        }

    l3 = recent_game_window(clean, 3)
    l5 = recent_game_window(clean, 5)
    season_avg = statistics.mean(clean)
    l3_avg = statistics.mean(l3) if l3 else season_avg
    l5_avg = statistics.mean(l5) if l5 else season_avg
    # Heavy recent weight so a 41% season snap / 78% last-3 usage pattern moves.
    base = _weighted_mean([l3_avg, l5_avg, season_avg], [0.50, 0.30, 0.20])
    env = _script_multiplier(
        market_type=market_type,
        team_spread=team_spread,
        team_implied_total=team_implied_total,
    )
    projection = max(0.0, base * env)
    std = statistics.pstdev(clean) if len(clean) >= 2 else max(projection * 0.35, 0.8)
    if market_type in COUNT_MARKETS:
        std = max(std, math.sqrt(max(projection, 0.4)))
    else:
        std = max(std, max(projection * 0.22, 8.0) if "yds" in market_type else std)

    role_shift = None
    if season_avg > 0.15:
        role_shift = round((l3_avg - season_avg) / season_avg, 3)
    n = len(clean)
    if n >= 6 and abs(role_shift or 0) < 0.35:
        confidence = "high"
    elif n >= 3:
        confidence = "medium"
    else:
        confidence = "low"
    if injury_note:
        confidence = "low" if confidence != "high" else "medium"

    return {
        "model_projection": round(projection, 3),
        "median_outcome": round(float(statistics.median(clean)), 3),
        "std_dev": round(float(std), 3),
        "sample_games": n,
        "projection_confidence": confidence,
        "l3_avg": round(l3_avg, 3),
        "season_avg": round(season_avg, 3),
        "role_shift": role_shift,
        "env_multiplier": env,
        "injury_note": injury_note,
    }


def nfl_side_probabilities(
    line: float,
    *,
    market_type: str,
    projection: float,
    std_dev: float | None,
    empirical_values: list[float] | None = None,
) -> dict[str, float]:
    if uses_normal_distribution(market_type) and std_dev is not None:
        sigma = max(float(std_dev), 0.5)
        # Continuity: P(over half-line) ≈ 1 - Φ(line)
        over = 1.0 - _norm_cdf(float(line), float(projection), sigma)
        under = _norm_cdf(float(line), float(projection), sigma)
        if empirical_values:
            emp_over = sum(1 for v in empirical_values if v > line)
            emp_under = sum(1 for v in empirical_values if v < line)
            counted = emp_over + emp_under
            if counted >= 3:
                over = 0.65 * over + 0.35 * (emp_over / counted)
                under = 0.65 * under + 0.35 * (emp_under / counted)
        total = over + under
        if total > 0:
            over /= total
            under /= total
        return {
            "model_probability_over": round(max(0.001, min(0.999, over)), 4),
            "model_probability_under": round(max(0.001, min(0.999, under)), 4),
        }
    return model_probabilities(
        line,
        projection=projection,
        std_dev=std_dev,
        empirical_values=empirical_values,
    )


def market_fair_probs(over_odds: int | None, under_odds: int | None) -> dict[str, float | None]:
    if over_odds is not None and under_odds is not None:
        try:
            fair_over, fair_under = market_probs_from_american_totals(int(over_odds), int(under_odds))
            return {
                "market_probability_over": round(fair_over, 4),
                "market_probability_under": round(fair_under, 4),
            }
        except (TypeError, ValueError):
            pass
    out: dict[str, float | None] = {
        "market_probability_over": None,
        "market_probability_under": None,
    }
    if over_odds is not None:
        try:
            out["market_probability_over"] = round(american_to_implied_prob(int(over_odds)), 4)
        except (TypeError, ValueError):
            pass
    if under_odds is not None:
        try:
            out["market_probability_under"] = round(american_to_implied_prob(int(under_odds)), 4)
        except (TypeError, ValueError):
            pass
    return out


def score_nfl_prop(
    *,
    edge: float | None,
    sample_games: int,
    role_shift: float | None,
    projection_confidence: str,
    injury_note: str | None,
) -> dict[str, Any]:
    """NFL ranking score — not the MLB L5/L10 formula."""
    base = 50.0
    if edge is not None:
        base += max(-18.0, min(28.0, edge * 220.0))
    sample = min(12.0, sample_games * 1.6)
    base += sample
    if projection_confidence == "high":
        base += 8.0
    elif projection_confidence == "medium":
        base += 4.0
    if role_shift is not None and abs(role_shift) >= 0.35:
        # Recent role disagrees with season average — prefer the recent signal,
        # but haircut confidence rather than inventing extra edge.
        base -= 4.0
    if injury_note:
        base -= 8.0
    score = round(max(0.0, min(100.0, base)), 1)
    if (
        score >= ELITE_SCORE
        and (edge or 0) >= ELITE_EDGE
        and sample_games >= 4
        and not injury_note
    ):
        tier = "elite"
    elif score >= VERY_STRONG_SCORE and (edge or 0) >= VERY_STRONG_EDGE and sample_games >= 3:
        tier = "very_strong"
    elif score >= MIN_PROP_SCORE and (edge or 0) >= MIN_EDGE:
        tier = "strong"
    elif score >= 55:
        tier = "moderate"
    else:
        tier = "low"
    return {
        "prop_score": score,
        "line_strength": tier,
        "actionable": score >= MIN_PROP_SCORE and (edge or 0) >= MIN_EDGE and sample_games >= 3,
    }
