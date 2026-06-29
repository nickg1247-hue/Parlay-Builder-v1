"""Shared thresholds for edges and UI."""

DEFAULT_MIN_EDGE = 0.08
# Actionable ML singles also require model_prob_side >= 55%, or >= 52% with edge >= 10%.
# See app/models/ml_pick_gates.py and DEV.md.
