"""Individual scoring components for the prop engine."""

from __future__ import annotations

import statistics
from typing import Any

from app.odds.odds_math import american_to_implied_prob, market_probs_from_american_totals
from app.services.prop_engine.constants import MIN_EDGE
from app.services.prop_engine.utils import recent_game_window


def score_recent_form(
    *,
    side: str,
    l5_rate: float | None,
    l10_rate: float | None,
    season_rate: float | None,
    values: list[float],
    line: float,
) -> tuple[float, dict[str, Any]]:
    l3 = recent_game_window(values, 3)
    l3_over = _hit_rate(l3, line, side) if l3 else None

    rates = [r for r in (l3_over, l5_rate, l10_rate, season_rate) if r is not None]
    if not rates:
        return 0.0, {"l3_hit_rate": l3_over}

    base = (
        (l3_over or 0) * 0.30
        + (l5_rate or 0) * 0.25
        + (l10_rate or 0) * 0.25
        + (season_rate or 0) * 0.20
    ) * 100.0

    trend = 0.0
    if l3_over is not None and l10_rate is not None:
        trend = (l3_over - l10_rate) * 15.0

    score = max(0.0, min(100.0, base + trend))
    return round(score, 1), {
        "l3_hit_rate": l3_over,
        "trend_direction": "up" if trend > 2 else "down" if trend < -2 else "flat",
    }


def score_matchup(
    *,
    market_type: str,
    opposing_pitcher_era: float | None,
    pitcher_k_rate: float | None,
    matchup_notes: list[str],
) -> tuple[float, dict[str, Any]]:
    score = 50.0
    if market_type.startswith("batter_") and opposing_pitcher_era is not None:
        era = opposing_pitcher_era
        if era >= 5.0:
            score = 88.0
        elif era >= 4.5:
            score = 78.0
        elif era <= 2.9:
            score = 28.0
        elif era <= 3.2:
            score = 38.0
        else:
            score = 50.0 + max(-12.0, min(12.0, (era - 3.85) / 0.65 * 12.0))
    elif market_type == "pitcher_strikeouts" and pitcher_k_rate is not None:
        if pitcher_k_rate >= 7.0:
            score = 85.0
        elif pitcher_k_rate >= 6.0:
            score = 75.0
        elif pitcher_k_rate <= 3.5:
            score = 35.0
        elif pitcher_k_rate <= 4.5:
            score = 45.0
        else:
            score = 55.0 + (pitcher_k_rate - 5.0) * 8.0

    return round(max(0.0, min(100.0, score)), 1), {"matchup_notes": matchup_notes}


def score_role_usage(
    *,
    sample_games: int,
    market_type: str,
) -> tuple[float, dict[str, Any]]:
    flags: list[str] = []
    if sample_games < 5:
        return 20.0, {"role_flags": ["Insufficient sample — role uncertain"]}
    if sample_games < 10:
        flags.append("Limited season sample")
        score = 55.0
    elif sample_games >= 20:
        score = 85.0
    else:
        score = 65.0 + (sample_games - 10) * 2.0

    if market_type.startswith("pitcher_") and sample_games < 8:
        score -= 15.0
        flags.append("Pitcher workload sample thin")

    return round(max(0.0, min(100.0, score)), 1), {"role_flags": flags}


def score_line_value(
    *,
    side: str,
    projection: float | None,
    median: float | None,
    line: float,
    std_dev: float | None,
) -> tuple[float, dict[str, Any]]:
    if projection is None:
        return 0.0, {}

    margin = (projection - line) if side == "over" else (line - projection)
    std = std_dev or max(abs(projection), 0.5)
    z = margin / std

    if margin <= 0:
        return max(0.0, 20.0 + z * 10.0), {"line_margin": round(margin, 3)}

    if z >= 1.5:
        score = 95.0
    elif z >= 1.0:
        score = 85.0
    elif z >= 0.5:
        score = 72.0
    else:
        score = 55.0 + z * 20.0

    med_margin = None
    if median is not None:
        med_margin = (median - line) if side == "over" else (line - median)

    return round(max(0.0, min(100.0, score)), 1), {
        "line_margin": round(margin, 3),
        "median_margin": round(med_margin, 3) if med_margin is not None else None,
    }


def score_market_edge(
    *,
    model_prob: float,
    market_prob: float | None,
) -> tuple[float, float | None]:
    edge = compute_side_edge(model_prob, market_prob)
    return market_edge_score_from_edge(edge), edge


def score_consistency(
    *,
    values: list[float],
    line: float,
    side: str,
) -> tuple[float, dict[str, Any]]:
    if len(values) < 3:
        return 40.0, {"volatility": "high"}

    std = statistics.pstdev(values)
    mean = statistics.mean(values) or 0.01
    cv = std / abs(mean) if mean else std

    l10 = recent_game_window(values, 10)
    margins = []
    for stat in l10:
        if side == "over":
            margins.append(stat - line)
        else:
            margins.append(line - stat)
    avg_margin = statistics.mean(margins) if margins else 0.0

    score = 70.0
    if cv <= 0.35:
        score += 20.0
    elif cv <= 0.55:
        score += 10.0
    elif cv >= 1.0:
        score -= 25.0
    elif cv >= 0.75:
        score -= 12.0

    if avg_margin >= 0.75:
        score += 10.0
    elif avg_margin <= 0.1:
        score -= 15.0

    vol = "low" if cv <= 0.45 else "medium" if cv <= 0.75 else "high"
    return round(max(0.0, min(100.0, score)), 1), {
        "std_dev": round(std, 3),
        "coefficient_of_variation": round(cv, 3),
        "avg_win_margin_l10": round(avg_margin, 3),
        "volatility": vol,
    }


def score_context(**_kwargs: Any) -> tuple[float, dict[str, Any]]:
    """Placeholder context score — neutral until weather/travel feeds wired."""
    return 50.0, {"context_notes": []}


def _hit_rate(values: list[float], line: float, side: str) -> float | None:
    if not values:
        return None
    if side == "over":
        hits = sum(1 for v in values if v > line)
    else:
        hits = sum(1 for v in values if v < line)
    return round(hits / len(values), 3)


def compute_side_edge(
    model_prob: float,
    market_prob: float | None,
) -> float | None:
    if market_prob is None:
        return None
    return round(model_prob - market_prob, 4)


def market_edge_score_from_edge(edge: float | None) -> float:
    if edge is None:
        return 0.0
    if edge <= 0:
        return max(0.0, 30.0 + edge * 200.0)
    if edge < MIN_EDGE:
        return 40.0 + (edge / MIN_EDGE) * 20.0
    if edge >= 0.12:
        return 100.0
    if edge >= 0.08:
        return 88.0
    return 60.0 + (edge - MIN_EDGE) / (0.08 - MIN_EDGE) * 28.0
