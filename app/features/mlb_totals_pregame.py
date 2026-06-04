"""Leakage-safe pregame features for MLB totals (combined runs) model."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from app.config import PROJECT_ROOT
from app.data.mlb_games import load_games_with_totals
from app.data.pitcher_lookup import lookup_pitcher_profile
from app.features.mlb_pregame import (
    DEFAULT_IP,
    DEFAULT_PARK_FACTOR,
    DEFAULT_WHIP,
    _compute_rest_days,
    _load_park_factors,
)

PARK_FACTORS_PATH = PROJECT_ROOT / "app" / "data" / "park_factors.csv"

NEUTRAL_RUNS_PG = 4.5
H2H_MEETINGS = 5

TOTALS_FEATURE_COLUMNS = [
    "home_season_runs_scored_pg",
    "home_season_runs_allowed_pg",
    "away_season_runs_scored_pg",
    "away_season_runs_allowed_pg",
    "home_last10_runs_scored_pg",
    "home_last10_runs_allowed_pg",
    "away_last10_runs_scored_pg",
    "away_last10_runs_allowed_pg",
    "home_last30_runs_scored_pg",
    "home_last30_runs_allowed_pg",
    "away_last30_runs_scored_pg",
    "away_last30_runs_allowed_pg",
    "home_home_split_runs_scored_pg",
    "home_home_split_runs_allowed_pg",
    "away_away_split_runs_scored_pg",
    "away_away_split_runs_allowed_pg",
    "park_factor_runs",
    "home_pitcher_era",
    "away_pitcher_era",
    "home_pitcher_whip",
    "away_pitcher_whip",
    "home_pitcher_ip",
    "away_pitcher_ip",
    "home_rest_days",
    "away_rest_days",
    "h2h_avg_total_runs",
]


@dataclass
class _RunsRecord:
    date: pd.Timestamp
    team: str
    runs_scored: int
    runs_allowed: int
    is_home: bool
    season: int
    opponent: str
    total_runs: int


class _RunsTracker:
    def __init__(self) -> None:
        self.records: list[_RunsRecord] = []

    def games_before(self, team: str, before: pd.Timestamp) -> list[_RunsRecord]:
        return [r for r in self.records if r.team == team and r.date < before]

    def h2h_before(
        self, team_a: str, team_b: str, before: pd.Timestamp, n: int = H2H_MEETINGS
    ) -> list[_RunsRecord]:
        prior = [
            r
            for r in self.records
            if r.date < before
            and (
                (r.team == team_a and r.opponent == team_b)
                or (r.team == team_b and r.opponent == team_a)
            )
        ]
        seen_dates: set[pd.Timestamp] = set()
        totals: list[int] = []
        for r in sorted(prior, key=lambda x: (x.date, x.team), reverse=True):
            if r.date in seen_dates:
                continue
            seen_dates.add(r.date)
            totals.append(r.total_runs)
            if len(totals) >= n:
                break
        return totals

    def update_from_result(
        self,
        game_date: pd.Timestamp,
        home_team: str,
        away_team: str,
        home_score: int,
        away_score: int,
        season: int,
    ) -> None:
        total = home_score + away_score
        self.records.append(
            _RunsRecord(
                game_date, home_team, home_score, away_score, True, season, away_team, total
            )
        )
        self.records.append(
            _RunsRecord(
                game_date, away_team, away_score, home_score, False, season, home_team, total
            )
        )


def _runs_pg(games: list[_RunsRecord], scored: bool) -> float:
    if not games:
        return NEUTRAL_RUNS_PG
    if scored:
        return sum(g.runs_scored for g in games) / len(games)
    return sum(g.runs_allowed for g in games) / len(games)


def _season_games(games: list[_RunsRecord], season: int) -> list[_RunsRecord]:
    return [g for g in games if g.season == season]


def _team_runs_block(
    team: str,
    before: pd.Timestamp,
    season: int,
    tracker: _RunsTracker,
    is_home_team: bool,
) -> dict[str, float]:
    prior = tracker.games_before(team, before)
    season_g = _season_games(prior, season)
    last10 = prior[-10:]
    last30 = prior[-30:]
    home_g = [g for g in prior if g.is_home]
    away_g = [g for g in prior if not g.is_home]

    prefix = "home" if is_home_team else "away"
    split_scored_col = (
        f"{prefix}_home_split_runs_scored_pg"
        if is_home_team
        else f"{prefix}_away_split_runs_scored_pg"
    )
    split_allowed_col = (
        f"{prefix}_home_split_runs_allowed_pg"
        if is_home_team
        else f"{prefix}_away_split_runs_allowed_pg"
    )
    split_g = home_g if is_home_team else away_g

    return {
        f"{prefix}_season_runs_scored_pg": _runs_pg(season_g, True),
        f"{prefix}_season_runs_allowed_pg": _runs_pg(season_g, False),
        f"{prefix}_last10_runs_scored_pg": _runs_pg(last10, True),
        f"{prefix}_last10_runs_allowed_pg": _runs_pg(last10, False),
        f"{prefix}_last30_runs_scored_pg": _runs_pg(last30, True),
        f"{prefix}_last30_runs_allowed_pg": _runs_pg(last30, False),
        split_scored_col: _runs_pg(split_g, True),
        split_allowed_col: _runs_pg(split_g, False),
    }


def build_totals_features(
    games_df: pd.DataFrame,
    era_medians: dict[int | str, float] | None = None,
    rest_fill: float = 1.0,
    update_state: bool = True,
) -> pd.DataFrame:
    df = games_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    if "total_runs" not in df.columns:
        if "home_score" in df.columns and "away_score" in df.columns:
            df["total_runs"] = df["home_score"] + df["away_score"]
    df = df.sort_values(["date", "game_id"]).reset_index(drop=True)

    if era_medians is None:
        era_medians = {"default": 4.0}

    park_map = _load_park_factors()
    tracker = _RunsTracker()
    rows: list[dict] = []

    for row in df.itertuples(index=False):
        game_date = pd.to_datetime(row.date)
        season = int(getattr(row, "season", game_date.year))
        before = game_date

        home_prior = tracker.games_before(row.home_team, before)
        away_prior = tracker.games_before(row.away_team, before)

        h2h = tracker.h2h_before(row.home_team, row.away_team, before)
        h2h_avg = sum(h2h) / len(h2h) if h2h else NEUTRAL_RUNS_PG * 2

        home_sp = getattr(row, "home_starting_pitcher", None)
        away_sp = getattr(row, "away_starting_pitcher", None)
        home_prof = lookup_pitcher_profile(home_sp, season, era_medians)
        away_prof = lookup_pitcher_profile(away_sp, season, era_medians)

        feats: dict = {
            "game_id": str(row.game_id),
            "date": game_date,
            "home_team": row.home_team,
            "away_team": row.away_team,
            "season": season,
            "park_factor_runs": park_map.get(row.home_team, DEFAULT_PARK_FACTOR),
            "home_pitcher_era": home_prof["era"],
            "away_pitcher_era": away_prof["era"],
            "home_pitcher_whip": home_prof["whip"],
            "away_pitcher_whip": away_prof["whip"],
            "home_pitcher_ip": home_prof["ip"],
            "away_pitcher_ip": away_prof["ip"],
            "home_rest_days": _compute_rest_days(home_prior, game_date, rest_fill),
            "away_rest_days": _compute_rest_days(away_prior, game_date, rest_fill),
            "h2h_avg_total_runs": h2h_avg,
        }
        feats.update(_team_runs_block(row.home_team, before, season, tracker, True))
        feats.update(_team_runs_block(row.away_team, before, season, tracker, False))

        if hasattr(row, "total_runs") and pd.notna(getattr(row, "total_runs", None)):
            feats["total_runs"] = float(row.total_runs)
        if hasattr(row, "home_score"):
            feats["home_score"] = row.home_score
            feats["away_score"] = row.away_score

        rows.append(feats)

        if (
            update_state
            and hasattr(row, "home_score")
            and pd.notna(getattr(row, "home_score", None))
        ):
            tracker.update_from_result(
                game_date,
                row.home_team,
                row.away_team,
                int(row.home_score),
                int(row.away_score),
                season,
            )

    return pd.DataFrame(rows)


def build_totals_features_for_history(
    games_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    raw = games_df if games_df is not None else load_games_with_totals()
    return build_totals_features(raw, update_state=True)


def build_totals_features_for_slate(
    slate_df: pd.DataFrame,
    history_df: pd.DataFrame | None = None,
    era_medians: dict | None = None,
    rest_fill: float = 1.0,
) -> pd.DataFrame:
    hist = history_df if history_df is not None else load_games_with_totals()
    min_date = pd.to_datetime(slate_df["date"]).min()
    hist = hist[hist["date"] < min_date].copy()
    combined = pd.concat([hist, slate_df], ignore_index=True)
    combined = combined.sort_values(["date", "game_id"]).reset_index(drop=True)
    feats = build_totals_features(
        combined, era_medians=era_medians, rest_fill=rest_fill, update_state=True
    )
    return feats.iloc[len(hist) :].copy().reset_index(drop=True)
