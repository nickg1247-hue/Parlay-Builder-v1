"""NBA totals features — wave2 scoring / pace columns (no same-day leakage)."""

from __future__ import annotations

from app.features.nba_pregame import (
    FEATURE_COLUMNS_WAVE2,
    build_features_for_history,
    build_features_for_slate,
)

TOTALS_FEATURE_COLUMNS = list(FEATURE_COLUMNS_WAVE2)

__all__ = [
    "TOTALS_FEATURE_COLUMNS",
    "build_features_for_history",
    "build_features_for_slate",
]
