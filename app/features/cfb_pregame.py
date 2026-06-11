"""CFB pregame features — Elo, season win %, rest, scoring; no same-day leakage."""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass

import pandas as pd

from app.odds.cfb_team_aliases import normalize_team_name

NEUTRAL_SEASON_WIN_PCT = 0.5
DEFAULT_REST_FILL = 7.0
DEFAULT_PTS_FILL = 28.0

FEATURE_COLUMNS = [
    "elo_diff",
    "home_season_win_pct",
    "away_season_win_pct",
    "home_rest_days",
    "away_rest_days",
    "rest_diff",
    "home_field",
    "home_b2b",
    "away_b2b",
]

MARGIN_FEATURE_COLUMNS = [
    "elo_diff",
    "home_season_win_pct",
    "away_season_win_pct",
    "home_rest_days",
    "away_rest_days",
    "rest_diff",
    "home_season_pts_for",
    "away_season_pts_for",
    "home_season_pts_against",
    "away_season_pts_against",
    "home_season_margin_avg",
    "away_season_margin_avg",
]

TOTALS_FEATURE_COLUMNS = list(MARGIN_FEATURE_COLUMNS)


@dataclass
class _GameRecord:
    date: pd.Timestamp
    team: str
    win: int
    season: int
    pts_for: int | None = None
    pts_against: int | None = None


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
        *,
        home_score: int | None = None,
        away_score: int | None = None,
    ) -> None:
        for team, win, pts_for, pts_against in (
            (home_team, int(home_win), home_score, away_score),
            (away_team, 1 - int(home_win), away_score, home_score),
        ):
            self._records[team].append(
                _GameRecord(game_date, team, win, season, pts_for, pts_against)
            )
            self._dates[team].append(game_date)


def build_team_tracker_from_history(games_df: pd.DataFrame) -> _TeamTracker:
    tracker = _TeamTracker()
    df = games_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    for row in df.sort_values(["date", "game_id"]).itertuples(index=False):
        if pd.isna(getattr(row, "home_win", None)):
            continue
        home_score = (
            int(row.home_score)
            if hasattr(row, "home_score") and pd.notna(row.home_score)
            else None
        )
        away_score = (
            int(row.away_score)
            if hasattr(row, "away_score") and pd.notna(row.away_score)
            else None
        )
        tracker.update_from_result(
            pd.to_datetime(row.date),
            row.home_team,
            row.away_team,
            int(row.home_win),
            int(row.season),
            home_score=home_score,
            away_score=away_score,
        )
    return tracker


def _win_pct(games: list[_GameRecord]) -> float:
    if not games:
        return NEUTRAL_SEASON_WIN_PCT
    return sum(g.win for g in games) / len(games)


def _season_games(games: list[_GameRecord], season: int) -> list[_GameRecord]:
    return [g for g in games if g.season == season]


def _avg_pts_for(games: list[_GameRecord], pts_fill: float) -> float:
    vals = [g.pts_for for g in games if g.pts_for is not None]
    if not vals:
        return pts_fill
    return sum(vals) / len(vals)


def _avg_pts_against(games: list[_GameRecord], pts_fill: float) -> float:
    vals = [g.pts_against for g in games if g.pts_against is not None]
    if not vals:
        return pts_fill
    return sum(vals) / len(vals)


def _margin_avg(games: list[_GameRecord]) -> float:
    margins = [
        float(g.pts_for - g.pts_against)
        for g in games
        if g.pts_for is not None and g.pts_against is not None
    ]
    if not margins:
        return 0.0
    return sum(margins) / len(margins)


def _row_features(
    row,
    tracker: _TeamTracker,
    rest_fill: float,
    *,
    pts_fill: float = DEFAULT_PTS_FILL,
    include_scoring: bool = False,
) -> dict[str, float | str | int]:
    game_date = pd.to_datetime(row.date)
    before = game_date
    season = int(row.season)
    home_team = normalize_team_name(str(row.home_team))
    away_team = normalize_team_name(str(row.away_team))
    home_prior = tracker.games_before(home_team, before)
    away_prior = tracker.games_before(away_team, before)
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

    elo_home = float(getattr(row, "elo_home_pre", 1500.0) or 1500.0)
    elo_away = float(getattr(row, "elo_away_pre", 1500.0) or 1500.0)

    feats: dict[str, float | str | int] = {
        "game_id": str(row.game_id),
        "date": game_date,
        "home_team": home_team,
        "away_team": away_team,
        "season": season,
        "home_rest_days": home_rest,
        "away_rest_days": away_rest,
        "home_b2b": home_b2b,
        "away_b2b": away_b2b,
        "home_season_win_pct": home_season,
        "away_season_win_pct": away_season,
        "rest_diff": home_rest - away_rest,
        "home_field": 1,
        "elo_home_pre": elo_home,
        "elo_away_pre": elo_away,
        "elo_diff": elo_home - elo_away,
    }

    if include_scoring:
        feats["home_season_pts_for"] = (
            _avg_pts_for(home_season_g, pts_fill) if home_season_g else pts_fill
        )
        feats["away_season_pts_for"] = (
            _avg_pts_for(away_season_g, pts_fill) if away_season_g else pts_fill
        )
        feats["home_season_pts_against"] = (
            _avg_pts_against(home_season_g, pts_fill) if home_season_g else pts_fill
        )
        feats["away_season_pts_against"] = (
            _avg_pts_against(away_season_g, pts_fill) if away_season_g else pts_fill
        )
        feats["home_season_margin_avg"] = _margin_avg(home_season_g)
        feats["away_season_margin_avg"] = _margin_avg(away_season_g)

    return feats


def build_features(
    games_df: pd.DataFrame,
    *,
    rest_fill: float = DEFAULT_REST_FILL,
    pts_fill: float = DEFAULT_PTS_FILL,
    update_state: bool = True,
    tracker: _TeamTracker | None = None,
    attach_elo: bool = True,
    include_scoring: bool = False,
) -> pd.DataFrame:
    df = games_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["date", "game_id"]).reset_index(drop=True)
    state = tracker if tracker is not None else _TeamTracker()
    rows: list[dict] = []

    for row in df.itertuples(index=False):
        feats = _row_features(
            row,
            state,
            rest_fill,
            pts_fill=pts_fill,
            include_scoring=include_scoring,
        )
        if hasattr(row, "home_win") and pd.notna(getattr(row, "home_win", None)):
            feats["home_win"] = int(row.home_win)
        rows.append(feats)

        if update_state and hasattr(row, "home_win") and pd.notna(row.home_win):
            home_score = (
                int(row.home_score)
                if hasattr(row, "home_score") and pd.notna(row.home_score)
                else None
            )
            away_score = (
                int(row.away_score)
                if hasattr(row, "away_score") and pd.notna(row.away_score)
                else None
            )
            state.update_from_result(
                pd.to_datetime(row.date),
                normalize_team_name(str(row.home_team)),
                normalize_team_name(str(row.away_team)),
                int(row.home_win),
                int(row.season),
                home_score=home_score,
                away_score=away_score,
            )

    out = pd.DataFrame(rows)
    if attach_elo and "home_win" in out.columns and out["home_win"].notna().all():
        from app.models.cfb_baseline import attach_elo_features

        out = attach_elo_features(out)
        out["elo_diff"] = out["elo_home_pre"] - out["elo_away_pre"]
    elif attach_elo:
        from app.models.cfb_baseline import attach_elo_for_slate

        out = attach_elo_for_slate(out)
        out["elo_diff"] = out["elo_home_pre"] - out["elo_away_pre"]
    return out


def _train_imputation_fills(games: pd.DataFrame) -> tuple[float, float]:
    train = games[games["season"].isin([2022, 2023, 2024])]
    rest_fill = float(
        pd.concat([train["home_rest_days"], train["away_rest_days"]]).median()
    )
    if pd.isna(rest_fill):
        rest_fill = DEFAULT_REST_FILL
    scored = train[train["home_score"].notna() & train["away_score"].notna()]
    if scored.empty:
        pts_fill = DEFAULT_PTS_FILL
    else:
        pts_fill = float(
            pd.concat([scored["home_score"], scored["away_score"]]).median()
        )
        if pd.isna(pts_fill):
            pts_fill = DEFAULT_PTS_FILL
    return rest_fill, pts_fill


def build_features_for_history(games_df: pd.DataFrame | None = None) -> pd.DataFrame:
    from app.models.cfb_baseline import load_games

    games = games_df if games_df is not None else load_games()
    rest_fill, _ = _train_imputation_fills(games)
    return build_features(games, rest_fill=rest_fill)


def _build_slate_features(
    slate_rows: pd.DataFrame,
    history_df: pd.DataFrame | None,
    *,
    include_scoring: bool,
) -> pd.DataFrame:
    from app.models.cfb_baseline import load_games

    hist = history_df if history_df is not None else load_games()
    hist = hist[hist["home_win"].notna()].copy()
    hist["date"] = pd.to_datetime(hist["date"])
    hist["home_team"] = hist["home_team"].map(normalize_team_name)
    hist["away_team"] = hist["away_team"].map(normalize_team_name)
    rest_fill, pts_fill = _train_imputation_fills(hist)

    slate = slate_rows.copy()
    slate["home_team"] = slate["home_team"].map(normalize_team_name)
    slate["away_team"] = slate["away_team"].map(normalize_team_name)
    slate["date"] = pd.to_datetime(slate["date"])
    if "season" not in slate.columns:
        slate["season"] = slate["date"].apply(
            lambda d: d.year if pd.Timestamp(d).month >= 8 else pd.Timestamp(d).year - 1
        )
    slate["home_rest_days"] = rest_fill
    slate["away_rest_days"] = rest_fill
    slate["home_b2b"] = 0
    slate["away_b2b"] = 0

    slate_ids = set(slate["game_id"].astype(str))
    slate_min_date = pd.to_datetime(slate["date"]).min()
    hist = hist[~hist["game_id"].astype(str).isin(slate_ids)].copy()
    hist_before = hist[hist["date"] < slate_min_date].copy()
    tracker = build_team_tracker_from_history(hist_before)
    combined = pd.concat([hist_before, slate], ignore_index=True, sort=False)
    combined["date"] = pd.to_datetime(combined["date"])
    combined = combined.sort_values(["date", "game_id"]).reset_index(drop=True)
    full = build_features(
        combined,
        rest_fill=rest_fill,
        pts_fill=pts_fill,
        tracker=tracker,
        attach_elo=True,
        include_scoring=include_scoring,
    )
    return full[full["game_id"].astype(str).isin(slate_ids)].drop_duplicates(
        subset=["game_id"], keep="last"
    ).copy()


def build_features_for_slate(
    slate_rows: pd.DataFrame,
    history_df: pd.DataFrame | None = None,
    *,
    rest_fill: float = DEFAULT_REST_FILL,
) -> pd.DataFrame:
    del rest_fill  # imputation from history
    return _build_slate_features(slate_rows, history_df, include_scoring=False)


def build_margin_features_for_history(games_df: pd.DataFrame | None = None) -> pd.DataFrame:
    from app.models.cfb_baseline import load_games

    games = games_df if games_df is not None else load_games()
    rest_fill, pts_fill = _train_imputation_fills(games)
    return build_features(
        games,
        rest_fill=rest_fill,
        pts_fill=pts_fill,
        include_scoring=True,
    )


def build_margin_features_for_slate(
    slate_rows: pd.DataFrame,
    history_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    return _build_slate_features(slate_rows, history_df, include_scoring=True)


def build_totals_features_for_history(games_df: pd.DataFrame | None = None) -> pd.DataFrame:
    return build_margin_features_for_history(games_df)


def build_totals_features_for_slate(
    slate_rows: pd.DataFrame,
    history_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    return build_margin_features_for_slate(slate_rows, history_df)
