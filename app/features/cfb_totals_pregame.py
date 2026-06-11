"""CFB totals features — scoring columns shared with margin model."""

from __future__ import annotations

from app.features.cfb_pregame import (
    TOTALS_FEATURE_COLUMNS,
    build_totals_features_for_history,
    build_totals_features_for_slate,
)

__all__ = [
    "TOTALS_FEATURE_COLUMNS",
    "build_totals_features_for_history",
    "build_totals_features_for_slate",
]
