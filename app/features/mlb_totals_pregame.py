"""Leakage-safe pregame features for MLB totals (combined runs) model."""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache

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


@dataclass
class _H2HGame:
    date: pd.Timestamp
    total_runs: int


class _RunsTracker:
    """Per-team chronological indexes for O(log n) pre-game lookups."""

    def __init__(self) -> None:
        self._team_records: dict[str, list[_RunsRecord]] = defaultdict(list)
        self._team_dates: dict[str, list[pd.Timestamp]] = defaultdict(list)
        self._h2h: dict[tuple[str, str], list[_H2HGame]] = defaultdict(list)

    @staticmethod
    def _pair_key(team_a: str, team_b: str) -> tuple[str, str]:
        return (team_a, team_b) if team_a <= team_b else (team_b, team_a)

    def games_before(self, team: str, before: pd.Timestamp) -> list[_RunsRecord]:
        dates = self._team_dates.get(team)
        if not dates:
            return []
        idx = bisect_left(dates, before)
        return self._team_records[team][:idx]

    def h2h_before(
        self, team_a: str, team_b: str, before: pd.Timestamp, n: int = H2H_MEETINGS
    ) -> list[int]:
        key = self._pair_key(team_a, team_b)
        games = self._h2h.get(key)
        if not games:
            return []
        dates = [g.date for g in games]
        idx = bisect_left(dates, before)
        return [g.total_runs for g in games[:idx][-n:]]

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
        for team, scored, allowed, is_home, opp in (
            (home_team, home_score, away_score, True, away_team),
            (away_team, away_score, home_score, False, home_team),
        ):
            self._team_records[team].append(
                _RunsRecord(
                    game_date, team, scored, allowed, is_home, season, opp, total
                )
            )
            self._team_dates[team].append(game_date)

        key = self._pair_key(home_team, away_team)
        self._h2h[key].append(_H2HGame(game_date, total))


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


def build_runs_tracker_from_history(games_df: pd.DataFrame) -> _RunsTracker:
    """Single O(n) pass over completed games."""
    tracker = _RunsTracker()
    df = games_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["date", "game_id"])
    for row in df.itertuples(index=False):
        if not hasattr(row, "home_score") or pd.isna(getattr(row, "home_score", None)):
            continue
        game_date = pd.to_datetime(row.date)
        season = int(getattr(row, "season", game_date.year))
        tracker.update_from_result(
            game_date,
            row.home_team,
            row.away_team,
            int(row.home_score),
            int(row.away_score),
            season,
        )
    return tracker


@lru_cache(maxsize=16)
def _cached_tracker_before(cutoff_date_iso: str) -> _RunsTracker:
    hist = load_games_with_totals()
    hist = hist[hist["date"] < pd.Timestamp(cutoff_date_iso)].copy()
    return build_runs_tracker_from_history(hist)


def get_runs_tracker_before(before: pd.Timestamp) -> _RunsTracker:
    return _cached_tracker_before(pd.to_datetime(before).isoformat())


def _row_features(
    row,
    tracker: _RunsTracker,
    park_map: dict[str, float],
    era_medians: dict[int | str, float],
    rest_fill: float,
) -> dict:
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
    return feats


def build_totals_features(
    games_df: pd.DataFrame,
    era_medians: dict[int | str, float] | None = None,
    rest_fill: float = 1.0,
    update_state: bool = True,
    tracker: _RunsTracker | None = None,
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
    state = tracker if tracker is not None else _RunsTracker()
    rows: list[dict] = []

    for row in df.itertuples(index=False):
        feats = _row_features(row, state, park_map, era_medians, rest_fill)

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
            game_date = pd.to_datetime(row.date)
            season = int(getattr(row, "season", game_date.year))
            state.update_from_result(
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
    """Score slate rows only; history precomputed in one O(n) pass (cached by date)."""
    del history_df  # unused — always use cached tracker (avoids per-request O(n) rebuild)
    slate = slate_df.copy()
    slate["date"] = pd.to_datetime(slate["date"])
    min_date = slate["date"].min()
    tracker = get_runs_tracker_before(min_date)

    if era_medians is None:
        era_medians = {"default": 4.0}

    park_map = _load_park_factors()
    rows = [
        _row_features(row, tracker, park_map, era_medians, rest_fill)
        for row in slate.sort_values(["date", "game_id"]).itertuples(index=False)
    ]
    return pd.DataFrame(rows)
