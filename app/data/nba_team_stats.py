"""Load cached NBA team advanced stats (stats.nba.com) for custom model factors."""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from app.config import PROJECT_ROOT
from app.odds.nba_team_aliases import normalize_nba_team_name

logger = logging.getLogger(__name__)

TEAM_STATS_PARQUET = PROJECT_ROOT / "data" / "processed" / "nba_team_stats.parquet"

# Per-team season snapshot used by custom weighted model.
STAT_COLUMNS = (
    "off_rating",
    "def_rating",
    "pace",
    "reb_pct",
    "tov_pct",
    "fg3_pct",
    "ft_rate",
    "bench_pts_proxy",
)


def load_team_stats_table() -> pd.DataFrame | None:
    if not TEAM_STATS_PARQUET.exists():
        return None
    df = pd.read_parquet(TEAM_STATS_PARQUET)
    if df.empty:
        return None
    df = df.copy()
    df["team"] = df["team"].map(normalize_nba_team_name)
    return df


def team_stats_row(
    team: str,
    season: int,
    *,
    stats_df: pd.DataFrame | None = None,
) -> dict[str, float] | None:
    table = stats_df if stats_df is not None else load_team_stats_table()
    if table is None or table.empty:
        return None
    team = normalize_nba_team_name(team)
    match = table[(table["team"] == team) & (table["season"] == int(season))]
    if match.empty:
        return None
    row = match.iloc[-1]
    out: dict[str, float] = {}
    for col in STAT_COLUMNS:
        if col in row.index and pd.notna(row[col]):
            out[col] = float(row[col])
    return out or None


def proxy_team_stats_from_features(feat: dict[str, Any]) -> tuple[dict[str, float], dict[str, float]]:
    """Approximate ORtg/DRTG/pace from rolling game history when advanced cache is missing."""
    league_pts = 110.0

    def side(prefix: str) -> dict[str, float]:
        pts_for = float(feat.get(f"{prefix}_season_pts_for", league_pts))
        pts_against = float(feat.get(f"{prefix}_season_pts_against", league_pts))
        l10_for = float(feat.get(f"{prefix}_last10_pts_for", pts_for))
        l10_against = float(feat.get(f"{prefix}_last10_pts_against", pts_against))
        ortg = 100.0 * pts_for / league_pts
        drtg = 100.0 * pts_against / league_pts
        pace = l10_for + l10_against
        bench_proxy = max(0.0, min(1.0, 0.5 + (l10_for - pts_for) / 20.0))
        return {
            "off_rating": ortg,
            "def_rating": drtg,
            "pace": pace,
            "reb_pct": 0.50,
            "tov_pct": 0.14,
            "fg3_pct": 0.36,
            "ft_rate": 0.25,
            "bench_pts_proxy": bench_proxy,
        }

    return side("home"), side("away")
