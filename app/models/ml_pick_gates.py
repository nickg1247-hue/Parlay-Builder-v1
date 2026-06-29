"""Moneyline pick quality gates for actionable singles."""

from __future__ import annotations

from app.models.constants import DEFAULT_MIN_EDGE

# Model win prob on the recommended side must clear one of these bars.
MIN_ACTIONABLE_MODEL_PROB = 0.55
MIN_ACTIONABLE_MODEL_PROB_WITH_HIGH_EDGE = 0.52
MIN_EDGE_FOR_LOWER_PROB_BAR = 0.10


def is_actionable_ml_pick(
    model_prob_side: float | None,
    edge: float | None,
    *,
    min_edge: float = DEFAULT_MIN_EDGE,
) -> bool:
    """True when a moneyline single clears edge + model-confidence gates."""
    if edge is None or edge < min_edge:
        return False
    if model_prob_side is None:
        return False
    prob = float(model_prob_side)
    if prob >= MIN_ACTIONABLE_MODEL_PROB:
        return True
    return prob >= MIN_ACTIONABLE_MODEL_PROB_WITH_HIGH_EDGE and float(edge) >= MIN_EDGE_FOR_LOWER_PROB_BAR
