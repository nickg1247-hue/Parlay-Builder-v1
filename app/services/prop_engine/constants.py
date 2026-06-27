"""Scoring weights and recommendation thresholds."""

from __future__ import annotations

SCORE_WEIGHTS: dict[str, float] = {
    "recent_form": 0.15,
    "matchup": 0.20,
    "role_usage": 0.15,
    "line_value": 0.15,
    "market_edge": 0.15,
    "consistency": 0.10,
    "context": 0.10,
}

MIN_GAMES_FOR_SCORE = 5
MIN_PROP_SCORE = 80.0
MIN_EDGE = 0.05
MIN_EDGE_VERY_STRONG = 0.08
MIN_EDGE_ELITE = 0.12
VERY_STRONG_EDGE = MIN_EDGE_VERY_STRONG
ELITE_EDGE = MIN_EDGE_ELITE

VERY_STRONG_SCORE = 85.0
ELITE_SCORE = 90.0

# Display grades for all scored props (sorted high → low on boards).
GRADE_STRONG = MIN_PROP_SCORE
GRADE_MODERATE = 70.0
GRADE_LOW = 60.0

VERY_STRONG_ROLE = 70.0
VERY_STRONG_MATCHUP = 70.0

ELITE_ROLE = 80.0
ELITE_MATCHUP = 80.0
ELITE_LINE_VALUE = 80.0

CONFIDENCE_TIERS = ("elite", "very_strong", "strong", "rejected")

NO_ELITE_MESSAGE = "No Elite Props Found Today."
