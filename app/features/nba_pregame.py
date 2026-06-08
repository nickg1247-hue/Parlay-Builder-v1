"""NBA pregame features — rolling strength, rest, B2B; no same-day leakage."""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass

import pandas as pd

NEUTRAL_LAST10_WIN_PCT = 0.5
NEUTRAL_SEASON_WIN_PCT = 0.5
DEFAULT_REST_FILL = 2.0
LAST_N = 10

FEATURE_COLUMNS = [
    "home_rest_days",
    "away_rest_days",
    "home_b2b",
    "away_b2b",
    "home_last10_win_pct",
    "away_last10_win_pct",
    "home_season_win_pct",
    "away_season_win_pct",
    "elo_home_pre",
    "elo_away_pre",
]


@dataclass
class _GameRecord:
    date: pd.Timestamp
    team: str
    win: int
    season: int


class _TeamTracker:
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
        season: int,
    ) -> None:
        for team, win in (
            (home_team, int(home_win)),
            (away_team, 1 - int(home_win)),
        ):
            self._records[team].append(_GameRecord(game_date, team, win, season))
            self._dates[team].append(game_date)


def build_team_tracker_from_history(games_df: pd.DataFrame) -> _TeamTracker:
    tracker = _TeamTracker()
    df = games_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    for row in df.sort_values(["date", "game_id"]).itertuples(index=False):
        if pd.isna(getattr(row, "home_win", None)):
            continue
        tracker.update_from_result(
            pd.to_datetime(row.date),
            row.home_team,
            row.away_team,
            int(row.home_win),
            int(row.season),
        )
    return tracker


def _win_pct(games: list[_GameRecord]) -> float:
    if not games:
        return NEUTRAL_LAST10_WIN_PCT
    return sum(g.win for g in games) / len(games)


def _season_games(games: list[_GameRecord], season: int) -> list[_GameRecord]:
    return [g for g in games if g.season == season]


def _row_features(
    row,
    tracker: _TeamTracker,
    rest_fill: float,
) -> dict[str, float | str | int]:
    game_date = pd.to_datetime(row.date)
    before = game_date
    season = int(row.season)
    home_prior = tracker.games_before(row.home_team, before)
    away_prior = tracker.games_before(row.away_team, before)
    home_last10 = _win_pct(home_prior[-LAST_N:])
    away_last10 = _win_pct(away_prior[-LAST_N:])
    home_season_g = _season_games(home_prior, season)
    away_season_g = _season_games(away_prior, season)
    home_season = _win_pct(home_season_g) if home_season_g else NEUTRAL_SEASON_WIN_PCT
    away_season = _win_pct(away_season_g) if away_season_g else NEUTRAL_SEASON_WIN_PCT

    home_rest = (
        float(row.home_rest_days)
        if hasattr(row, "home_rest_days") and pd.notna(row.home_rest_days)
        else rest_fill
    )
    away_rest = (
        float(row.away_rest_days)
        if hasattr(row, "away_rest_days") and pd.notna(row.away_rest_days)
        else rest_fill
    )
    home_b2b = (
        int(row.home_b2b)
        if hasattr(row, "home_b2b") and pd.notna(row.home_b2b)
        else 0
    )
    away_b2b = (
        int(row.away_b2b)
        if hasattr(row, "away_b2b") and pd.notna(row.away_b2b)
        else 0
    )

    return {
        "game_id": str(row.game_id),
        "date": game_date,
        "home_team": row.home_team,
        "away_team": row.away_team,
        "season": season,
        "home_rest_days": home_rest,
        "away_rest_days": away_rest,
        "home_b2b": home_b2b,
        "away_b2b": away_b2b,
        "home_last10_win_pct": home_last10,
        "away_last10_win_pct": away_last10,
        "home_season_win_pct": home_season,
        "away_season_win_pct": away_season,
    }


def build_features(
    games_df: pd.DataFrame,
    *,
    rest_fill: float = DEFAULT_REST_FILL,
    update_state: bool = True,
    tracker: _TeamTracker | None = None,
    attach_elo: bool = True,
) -> pd.DataFrame:
    """Build features using only games strictly before each row's date."""
    df = games_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["date", "game_id"]).reset_index(drop=True)
    state = tracker if tracker is not None else _TeamTracker()
    rows: list[dict] = []

    for row in df.itertuples(index=False):
        feats = _row_features(row, state, rest_fill)
        if hasattr(row, "home_win") and pd.notna(getattr(row, "home_win", None)):
            feats["home_win"] = int(row.home_win)
        if hasattr(row, "home_score") and pd.notna(getattr(row, "home_score", None)):
            feats["home_score"] = int(row.home_score)
            feats["away_score"] = int(row.away_score)
        rows.append(feats)

        if update_state and hasattr(row, "home_win") and pd.notna(row.home_win):
            state.update_from_result(
                pd.to_datetime(row.date),
                row.home_team,
                row.away_team,
                int(row.home_win),
                int(row.season),
            )

    out = pd.DataFrame(rows)
    if attach_elo and "home_win" in out.columns and out["home_win"].notna().all():
        from app.models.nba_baseline import attach_elo_features

        out = attach_elo_features(out)
    return out


def build_features_for_history(games_df: pd.DataFrame | None = None) -> pd.DataFrame:
    from app.models.nba_baseline import load_games

    games = games_df if games_df is not None else load_games()
    train = games[games["season"].isin([2024, 2025])]
    rest_fill = float(
        pd.concat([train["home_rest_days"], train["away_rest_days"]]).median()
    )
    if pd.isna(rest_fill):
        rest_fill = DEFAULT_REST_FILL
    return build_features(games, rest_fill=rest_fill)


def build_features_for_slate(
    slate_rows: pd.DataFrame,
    history_df: pd.DataFrame | None = None,
    *,
    rest_fill: float = DEFAULT_REST_FILL,
) -> pd.DataFrame:
    """Score a live slate: tracker seeded from completed history only."""
    from app.models.nba_baseline import load_games

    hist = history_df if history_df is not None else load_games()
    hist = hist[hist["home_win"].notna()].copy()
    tracker = build_team_tracker_from_history(hist)
    combined = pd.concat([hist, slate_rows], ignore_index=True, sort=False)
    combined["date"] = pd.to_datetime(combined["date"])
    combined = combined.sort_values(["date", "game_id"]).reset_index(drop=True)
    slate_ids = set(slate_rows["game_id"].astype(str))
    full = build_features(combined, rest_fill=rest_fill, tracker=tracker, attach_elo=False)
    slate = full[full["game_id"].astype(str).isin(slate_ids)].copy()
    from app.models.nba_baseline import attach_elo_for_slate

    return attach_elo_for_slate(slate, history=hist)
