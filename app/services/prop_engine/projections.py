"""Player stat projections from game logs and matchup context."""

from __future__ import annotations

import statistics
from typing import Any

from app.services.prop_engine.utils import recent_game_window


def _weighted_mean(values: list[float], weights: list[float]) -> float:
    if not values:
        return 0.0
    total_w = sum(weights[: len(values)])
    if total_w <= 0:
        return statistics.mean(values)
    return sum(v * w for v, w in zip(values, weights)) / total_w


def build_projection(
    values: list[float],
    *,
    market_type: str,
    opposing_pitcher_era: float | None = None,
    matchup_adjustment: float = 0.0,
) -> dict[str, Any]:
    """
    Estimate expected stat outcome from chronological game log.

    Uses weighted blend of L3/L5/L10/season — not sportsbook lines.
    """
    if not values:
        return {
            "model_projection": None,
            "median_outcome": None,
            "std_dev": None,
            "sample_games": 0,
            "projection_confidence": "low",
        }

    l3 = recent_game_window(values, 3)
    l5 = recent_game_window(values, 5)
    l10 = recent_game_window(values, 10)

    l3_avg = statistics.mean(l3) if l3 else statistics.mean(values)
    l5_avg = statistics.mean(l5) if l5 else statistics.mean(values)
    l10_avg = statistics.mean(l10) if l10 else statistics.mean(values)
    season_avg = statistics.mean(values)

    base = _weighted_mean(
        [l3_avg, l5_avg, l10_avg, season_avg],
        [0.35, 0.25, 0.25, 0.15],
    )

    # Matchup tilt for batters vs pitcher quality
    tilt = 0.0
    if market_type.startswith("batter_") and opposing_pitcher_era is not None:
        era = opposing_pitcher_era
        if era >= 5.0:
            tilt = 0.12
        elif era >= 4.5:
            tilt = 0.08
        elif era <= 2.9:
            tilt = -0.12
        elif era <= 3.2:
            tilt = -0.08
        else:
            tilt = max(-0.06, min(0.06, (era - 3.85) / 0.65 * 0.06))
    elif market_type == "pitcher_strikeouts":
        tilt = max(-0.08, min(0.08, (l10_avg - season_avg) * 0.04))

    projection = max(0.0, base * (1.0 + tilt) + matchup_adjustment * 0.01)
    std = statistics.pstdev(values) if len(values) >= 2 else max(projection * 0.35, 0.5)
    median = statistics.median(values)

    confidence = "high" if len(values) >= 15 else "medium" if len(values) >= 8 else "low"

    return {
        "model_projection": round(projection, 3),
        "median_outcome": round(float(median), 3),
        "std_dev": round(std, 3),
        "sample_games": len(values),
        "projection_confidence": confidence,
        "recent_avg_l3": round(l3_avg, 3),
        "recent_avg_l5": round(l5_avg, 3),
        "recent_avg_l10": round(l10_avg, 3),
        "season_avg": round(season_avg, 3),
    }


def projection_supports_side(projection: float | None, line: float, side: str) -> bool:
    if projection is None:
        return False
    if side == "over":
        return projection > line
    return projection < line
