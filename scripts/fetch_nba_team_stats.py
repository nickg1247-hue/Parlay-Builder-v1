"""Fetch and cache NBA team advanced stats for the custom weighted model."""

from __future__ import annotations

import logging
import time
from typing import Any

import pandas as pd
from nba_api.stats.endpoints import leaguedashteamstats

from app.config import PROJECT_ROOT
from app.data.nba_team_stats import TEAM_STATS_PARQUET
from app.ingest.nba import _normalize_team_name

logger = logging.getLogger(__name__)

DEFAULT_SEASONS = ("2023-24", "2024-25", "2025-26")


def _season_end_year(season_label: str) -> int:
    return 2000 + int(season_label.split("-")[1])


def _fetch_season(season_label: str) -> pd.DataFrame:
    logger.info("Fetching advanced team stats for %s", season_label)
    resp = leaguedashteamstats.LeagueDashTeamStats(
        season=season_label,
        season_type_all_star="Regular Season",
        measure_type_detailed_defense="Advanced",
        per_mode_detailed="PerGame",
    )
    raw = resp.get_data_frames()[0]
    rows: list[dict[str, Any]] = []
    for _, row in raw.iterrows():
        team = _normalize_team_name(str(row.get("TEAM_NAME") or ""))
        if not team:
            continue
        reb = row.get("REB_PCT")
        rows.append(
            {
                "team": team,
                "season": _season_end_year(season_label),
                "season_label": season_label,
                "off_rating": float(row["OFF_RATING"]) if pd.notna(row.get("OFF_RATING")) else None,
                "def_rating": float(row["DEF_RATING"]) if pd.notna(row.get("DEF_RATING")) else None,
                "pace": float(row["PACE"]) if pd.notna(row.get("PACE")) else None,
                "reb_pct": float(reb) if reb is not None and pd.notna(reb) else None,
                "tov_pct": float(row["TM_TOV_PCT"]) if pd.notna(row.get("TM_TOV_PCT")) else None,
                "fg3_pct": float(row["FG3_PCT"]) if pd.notna(row.get("FG3_PCT")) else None,
                "ft_rate": float(row["FTA_RATE"]) if pd.notna(row.get("FTA_RATE")) else None,
                "bench_pts_proxy": None,
            }
        )
    return pd.DataFrame(rows)


def build_team_stats_cache(seasons: tuple[str, ...] = DEFAULT_SEASONS) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for season in seasons:
        try:
            frames.append(_fetch_season(season))
        except Exception as exc:
            logger.warning("Failed %s: %s", season, exc)
        time.sleep(0.6)
    if not frames:
        raise RuntimeError("No team stats fetched — check network / nba_api availability")
    out = pd.concat(frames, ignore_index=True)
    TEAM_STATS_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(TEAM_STATS_PARQUET, index=False)
    logger.info("Wrote %s rows to %s", len(out), TEAM_STATS_PARQUET)
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    build_team_stats_cache()
