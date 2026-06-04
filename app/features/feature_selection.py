"""Feature redundancy pruning via rank correlation."""

from __future__ import annotations

import pandas as pd

DEFAULT_CORR_THRESHOLD = 0.9


def drop_redundant_features(
    df: pd.DataFrame,
    columns: list[str],
    threshold: float = DEFAULT_CORR_THRESHOLD,
) -> tuple[list[str], list[str], pd.DataFrame]:
    """
    Greedy removal: keep earlier columns; drop later ones with Spearman |r| > threshold
    vs any kept column. Returns (kept, dropped, correlation_matrix).
    """
    present = [c for c in columns if c in df.columns]
    if len(present) < 2:
        return present, [], pd.DataFrame()

    corr = df[present].corr(method="spearman").abs()
    kept: list[str] = []
    dropped: list[str] = []

    for col in present:
        redundant = False
        for kept_col in kept:
            if corr.loc[col, kept_col] > threshold:
                redundant = True
                break
        if redundant:
            dropped.append(col)
        else:
            kept.append(col)

    return kept, dropped, corr
