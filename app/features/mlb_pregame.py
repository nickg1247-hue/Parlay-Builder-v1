"""Wave 1 pregame features — all computed from games strictly before game date."""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pandas as pd

from app.config import PROJECT_ROOT
from app.data.pitcher_lookup import lookup_pitcher_profile
from app.models.mlb_baseline import (
    FEATURE_COLUMNS,
    NEUTRAL_LAST10_RUN_DIFF,
    NEUTRAL_LAST10_WIN_PCT,
    attach_elo_features,
    load_games,
)

PARK_FACTORS_PATH = PROJECT_ROOT / "app" / "data" / "park_factors.csv"

WAVE1_EXTRA_COLUMNS = [
    "home_season_win_pct",
    "away_season_win_pct",
    "home_season_run_diff",
    "away_season_run_diff",
    "home_last30_win_pct",
    "away_last30_win_pct",
    "home_last30_run_diff",
    "away_last30_run_diff",
    "home_home_split_win_pct",
    "away_away_split_win_pct",
    "home_win_pct_rank",
    "away_win_pct_rank",
    "park_factor_runs",
    "home_pitcher_whip",
    "away_pitcher_whip",
    "home_pitcher_ip",
    "away_pitcher_ip",
]

FEATURE_COLUMNS_WAVE1 = FEATURE_COLUMNS + WAVE1_EXTRA_COLUMNS
FEATURE_COLUMNS_WAVE1_ELO = FEATURE_COLUMNS_WAVE1 + ["elo_home_pre", "elo_away_pre"]

NEUTRAL_WIN_PCT = 0.5
NEUTRAL_RUN_DIFF = 0.0
NEUTRAL_RANK = 15.5
DEFAULT_PARK_FACTOR = 1.0
DEFAULT_WHIP = 1.30
DEFAULT_IP = 150.0
MAX_REST_GAP_DAYS = 14


@dataclass
class _GameRecord:
    date: pd.Timestamp
    team: str
    win: int
    run_diff: int
    is_home: bool
    season: int


class _TeamTracker:
    """Per-team chronological indexes for O(log n) pre-game lookups."""

    def __init__(self) -> None:
        self._records: dict[str, list[_GameRecord]] = defaultdict(list)
        self._dates: dict[str, list[pd.Timestamp]] = defaultdict(list)

    def games_before(self, team: str, before: pd.Timestamp) -> list[_GameRecord]:
        dates = self._dates.get(team)
        if not dates:
            return []
        idx = bisect_left(dates, before)
        return self._records[team][:idx]

    def update_from_result(
        self,
        game_date: pd.Timestamp,
        home_team: str,
        away_team: str,
        home_win: int,
        home_rd: int,
        season: int,
    ) -> None:
        for team, win, rd, is_home in (
            (home_team, int(home_win), home_rd, True),
            (away_team, 1 - int(home_win), -home_rd, False),
        ):
            self._records[team].append(
                _GameRecord(game_date, team, win, rd, is_home, season)
            )
            self._dates[team].append(game_date)


def build_team_tracker_from_history(games_df: pd.DataFrame) -> _TeamTracker:
    tracker = _TeamTracker()
    df = games_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    for row in df.sort_values(["date", "game_id"]).itertuples(index=False):
        if not hasattr(row, "home_win") or pd.isna(getattr(row, "home_win", None)):
            continue
        game_date = pd.to_datetime(row.date)
        season = int(getattr(row, "season", game_date.year))
        home_rd = int(row.home_score) - int(row.away_score)
        tracker.update_from_result(
            game_date,
            row.home_team,
            row.away_team,
            int(row.home_win),
            home_rd,
            season,
        )
    return tracker


@lru_cache(maxsize=16)
def _cached_team_tracker_before(cutoff_date_iso: str) -> _TeamTracker:
    hist = load_games()
    hist = hist[hist["date"] < pd.Timestamp(cutoff_date_iso)].copy()
    return build_team_tracker_from_history(hist)


def get_team_tracker_before(before: pd.Timestamp) -> _TeamTracker:
    return _cached_team_tracker_before(pd.to_datetime(before).isoformat())


def _load_park_factors() -> dict[str, float]:
    df = pd.read_csv(PARK_FACTORS_PATH)
    return dict(zip(df["home_team"], df["park_factor_runs"].astype(float)))


def _rolling_stats(games: list[_GameRecord]) -> tuple[float, float]:
    if not games:
        return NEUTRAL_WIN_PCT, NEUTRAL_RUN_DIFF
    wins = [g.win for g in games]
    diffs = [g.run_diff for g in games]
    return sum(wins) / len(wins), sum(diffs) / len(diffs)


def _season_games(games: list[_GameRecord], season: int) -> list[_GameRecord]:
    return [g for g in games if g.season == season]


def _win_pct_ranks(
    all_teams: set[str], tracker: _TeamTracker, before: pd.Timestamp, season: int
) -> dict[str, float]:
    rates: dict[str, float] = {}
    for team in all_teams:
        sg = _season_games(tracker.games_before(team, before), season)
        if sg:
            rates[team] = sum(g.win for g in sg) / len(sg)
        else:
            rates[team] = NEUTRAL_WIN_PCT
    sorted_teams = sorted(rates.keys(), key=lambda t: rates[t], reverse=True)
    ranks = {team: float(i + 1) for i, team in enumerate(sorted_teams)}
    return ranks


def _team_feature_block(
    team: str,
    before: pd.Timestamp,
    season: int,
    tracker: _TeamTracker,
    all_teams: set[str],
    is_home_team: bool,
) -> dict[str, float]:
    prior = tracker.games_before(team, before)
    season_g = _season_games(prior, season)
    last30 = prior[-30:]
    home_g = [g for g in prior if g.is_home]
    away_g = [g for g in prior if not g.is_home]

    season_wp, season_rd = _rolling_stats(season_g)
    last30_wp, last30_rd = _rolling_stats(last30)
    home_split_wp, _ = _rolling_stats(home_g)
    away_split_wp, _ = _rolling_stats(away_g)
    ranks = _win_pct_ranks(all_teams, tracker, before, season)

    prefix = "home" if is_home_team else "away"
    split_col = f"{prefix}_home_split_win_pct" if is_home_team else f"{prefix}_away_split_win_pct"
    split_val = home_split_wp if is_home_team else away_split_wp

    return {
        f"{prefix}_season_win_pct": season_wp,
        f"{prefix}_season_run_diff": season_rd,
        f"{prefix}_last30_win_pct": last30_wp,
        f"{prefix}_last30_run_diff": last30_rd,
        split_col: split_val,
        f"{prefix}_win_pct_rank": ranks.get(team, NEUTRAL_RANK),
    }


def _last_n_from_prior(prior: list[_GameRecord], n: int) -> tuple[float, float]:
    if not prior:
        return NEUTRAL_LAST10_WIN_PCT, NEUTRAL_LAST10_RUN_DIFF
    window = prior[-n:]
    return _rolling_stats(window)


def _compute_rest_days(
    prior: list[_GameRecord],
    game_date: pd.Timestamp,
    rest_fill: float,
    season: int,
    max_gap: int = MAX_REST_GAP_DAYS,
) -> float:
    """
    Days since last game in the same season; use rest_fill when offseason/stale
    history would produce an implausible gap (> max_gap days) or no prior games
    exist in the current season.
    """
    if not prior:
        return rest_fill
    prior_in_season = [g for g in prior if g.season == season]
    if not prior_in_season:
        return rest_fill
    last = max(prior_in_season, key=lambda g: g.date).date
    gap = int((game_date - last).days)
    if gap > max_gap:
        return rest_fill
    return float(gap)


def _row_features(
    row,
    tracker: _TeamTracker,
    all_teams: set[str],
    park_map: dict[str, float],
    era_medians: dict[int | str, float],
    rest_fill: float,
    default_whip: float,
    default_ip: float,
) -> dict[str, float | str | None]:
    game_date = pd.to_datetime(row.date)
    season = int(getattr(row, "season", game_date.year))
    before = game_date

    home_prior = tracker.games_before(row.home_team, before)
    away_prior = tracker.games_before(row.away_team, before)
    home_l10_wp, home_l10_rd = _last_n_from_prior(home_prior, 10)
    away_l10_wp, away_l10_rd = _last_n_from_prior(away_prior, 10)

    home_sp = getattr(row, "home_starting_pitcher", None)
    away_sp = getattr(row, "away_starting_pitcher", None)
    home_prof = lookup_pitcher_profile(
        home_sp, season, era_medians, default_whip, default_ip
    )
    away_prof = lookup_pitcher_profile(
        away_sp, season, era_medians, default_whip, default_ip
    )

    feats: dict[str, float | str | None] = {
        "game_id": str(row.game_id),
        "date": game_date,
        "home_team": row.home_team,
        "away_team": row.away_team,
        "season": season,
        "home_pitcher_era": home_prof["era"],
        "away_pitcher_era": away_prof["era"],
        "home_pitcher_whip": home_prof["whip"],
        "away_pitcher_whip": away_prof["whip"],
        "home_pitcher_ip": home_prof["ip"],
        "away_pitcher_ip": away_prof["ip"],
        "home_last10_win_pct": home_l10_wp,
        "away_last10_win_pct": away_l10_wp,
        "home_last10_run_diff": home_l10_rd,
        "away_last10_run_diff": away_l10_rd,
        "home_rest_days": _compute_rest_days(
            home_prior, game_date, rest_fill, season
        ),
        "away_rest_days": _compute_rest_days(
            away_prior, game_date, rest_fill, season
        ),
        "park_factor_runs": park_map.get(row.home_team, DEFAULT_PARK_FACTOR),
    }
    feats.update(
        _team_feature_block(row.home_team, before, season, tracker, all_teams, True)
    )
    feats.update(
        _team_feature_block(row.away_team, before, season, tracker, all_teams, False)
    )
    return feats


def build_features(
    games_df: pd.DataFrame,
    era_medians: dict[int | str, float] | None = None,
    rest_fill: float = 1.0,
    default_whip: float = DEFAULT_WHIP,
    default_ip: float = DEFAULT_IP,
    update_state: bool = True,
    tracker: _TeamTracker | None = None,
) -> pd.DataFrame:
    """
    Build Wave 1 + legacy features for each row using only prior completed games.

    games_df must include: game_id, date, home_team, away_team, season.
    For training, include home_score, away_score, home_win.
    Pass update_state=False for live slate rows (do not ingest unknown outcomes).
    """
    df = games_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["date", "game_id"]).reset_index(drop=True)

    if era_medians is None:
        era_medians = {"default": 4.0}

    park_map = _load_park_factors()
    all_teams = set(df["home_team"]) | set(df["away_team"])
    state = tracker if tracker is not None else _TeamTracker()
    feature_rows: list[dict] = []

    for row in df.itertuples(index=False):
        feats = _row_features(
            row, state, all_teams, park_map, era_medians, rest_fill, default_whip, default_ip
        )
        if hasattr(row, "home_win") and pd.notna(getattr(row, "home_win", None)):
            feats["home_win"] = int(row.home_win)
        if hasattr(row, "home_score"):
            feats["home_score"] = row.home_score
            feats["away_score"] = row.away_score
        feature_rows.append(feats)

        if update_state and hasattr(row, "home_win") and pd.notna(row.home_win):
            game_date = pd.to_datetime(row.date)
            season = int(getattr(row, "season", game_date.year))
            home_rd = int(row.home_score) - int(row.away_score)
            state.update_from_result(
                game_date, row.home_team, row.away_team, int(row.home_win), home_rd, season
            )

    return pd.DataFrame(feature_rows)


def build_features_for_history(games_df: pd.DataFrame | None = None) -> pd.DataFrame:
    """Full historical feature matrix for training."""
    raw = games_df if games_df is not None else load_games()
    return build_features(raw, update_state=True)


def build_features_for_slate(
    slate_df: pd.DataFrame,
    history_df: pd.DataFrame | None = None,
    era_medians: dict | None = None,
    rest_fill: float = 1.0,
) -> pd.DataFrame:
    """Score slate rows only; history precomputed in one O(n) pass (cached by date)."""
    slate = slate_df.copy()
    slate["date"] = pd.to_datetime(slate["date"])
    min_date = slate["date"].min()

    if history_df is not None:
        tracker = build_team_tracker_from_history(
            history_df[pd.to_datetime(history_df["date"]) < min_date]
        )
    else:
        tracker = get_team_tracker_before(min_date)

    if era_medians is None:
        era_medians = {"default": 4.0}

    park_map = _load_park_factors()
    all_teams = set(slate["home_team"]) | set(slate["away_team"])
    rows = [
        _row_features(
            row,
            tracker,
            all_teams,
            park_map,
            era_medians,
            rest_fill,
            DEFAULT_WHIP,
            DEFAULT_IP,
        )
        for row in slate.sort_values(["date", "game_id"]).itertuples(index=False)
    ]
    return pd.DataFrame(rows)


def attach_elo_to_wave1(df: pd.DataFrame, history_df: pd.DataFrame | None = None) -> pd.DataFrame:
    """Add elo columns; requires home_win on historical rows for chronological Elo."""
    hist = history_df if history_df is not None else load_games()
    min_date = pd.to_datetime(df["date"]).min()
    hist = hist[hist["date"] < min_date]
    combined = pd.concat([hist, df], ignore_index=True).sort_values(["date", "game_id"])
    combined = attach_elo_features(combined)
    return combined.iloc[len(hist) :].copy()
