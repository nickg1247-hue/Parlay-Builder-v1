"""NFL pregame features — Elo, season win %, rest, home field, divisional.

No same-game leakage: win% / Elo use only games strictly before kickoff.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass

import pandas as pd

from app.features.nfl_stadiums import travel_miles
from app.ingest.nfl import (
    DEFAULT_REST_FILL,
    MAX_REST_GAP_DAYS,
    is_divisional,
    normalize_abbr,
)

NEUTRAL_SEASON_WIN_PCT = 0.5
DEFAULT_PTS_FILL = 22.0

FEATURE_COLUMNS = [
    "elo_diff",
    "home_season_win_pct",
    "away_season_win_pct",
    "home_rest_days",
    "away_rest_days",
    "rest_diff",
    "home_field",
    "divisional",
    "is_preseason",
]

FEATURE_COLUMNS_V2 = FEATURE_COLUMNS + [
    "home_l5_win_pct",
    "away_l5_win_pct",
    "home_l5_margin",
    "away_l5_margin",
    "home_streak",
    "away_streak",
    "home_home_win_pct",
    "away_road_win_pct",
    "short_rest_home",
    "short_rest_away",
    "bye_home",
    "bye_away",
    "week_norm",
    "margin_diff",
    "pyth_diff",
    "travel_miles",
]

MARGIN_FEATURE_COLUMNS = FEATURE_COLUMNS + [
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
    is_home: int = 0


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

    def last_game_date(self, team: str, before: pd.Timestamp) -> pd.Timestamp | None:
        prior = self.games_before(team, before)
        if not prior:
            return None
        return prior[-1].date

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
        for team, win, pts_for, pts_against, is_home in (
            (home_team, int(home_win), home_score, away_score, 1),
            (away_team, 1 - int(home_win), away_score, home_score, 0),
        ):
            self._records[team].append(
                _GameRecord(game_date, team, win, season, pts_for, pts_against, is_home)
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
            _team_key(row, "home"),
            _team_key(row, "away"),
            int(row.home_win),
            int(row.season),
            home_score=home_score,
            away_score=away_score,
        )
    return tracker


def _team_key(row, side: str) -> str:
    abbr_col = f"{side}_team_abbr"
    if hasattr(row, abbr_col):
        abbr = normalize_abbr(getattr(row, abbr_col))
        if abbr:
            return abbr
    name = getattr(row, f"{side}_team", "")
    return normalize_abbr(str(name)) or str(name)


def _win_pct(games: list[_GameRecord]) -> float:
    if not games:
        return NEUTRAL_SEASON_WIN_PCT
    return sum(g.win for g in games) / len(games)


def _last_n(games: list[_GameRecord], n: int) -> list[_GameRecord]:
    if not games:
        return []
    return games[-n:]


def _last_n_win_pct(games: list[_GameRecord], n: int) -> float:
    return _win_pct(_last_n(games, n))


def _last_n_margin(games: list[_GameRecord], n: int) -> float:
    return _margin_avg(_last_n(games, n))


def _streak(games: list[_GameRecord]) -> float:
    if not games:
        return 0.0
    last = games[-1].win
    count = 0
    for g in reversed(games):
        if g.win != last:
            break
        count += 1
    return float(count if last else -count)


def _split_win_pct(games: list[_GameRecord], *, is_home: int) -> float:
    subset = [g for g in games if int(g.is_home) == int(is_home)]
    return _win_pct(subset) if subset else NEUTRAL_SEASON_WIN_PCT


def _pythagorean(games: list[_GameRecord], pts_fill: float) -> float:
    pf = _avg_pts(games, "pts_for", pts_fill)
    pa = _avg_pts(games, "pts_against", pts_fill)
    exp = 2.37
    num = pf**exp
    den = num + (pa**exp)
    if den <= 0:
        return NEUTRAL_SEASON_WIN_PCT
    return float(num / den)


def _season_games(games: list[_GameRecord], season: int) -> list[_GameRecord]:
    return [g for g in games if g.season == season]


def _neutral_site(row) -> int:
    if hasattr(row, "neutral_site") and pd.notna(row.neutral_site):
        return int(row.neutral_site)
    return 0


def _is_preseason(row) -> int:
    game_type = str(getattr(row, "game_type", "") or "").lower()
    if game_type == "preseason":
        return 1
    if hasattr(row, "is_preseason") and pd.notna(getattr(row, "is_preseason", None)):
        return int(row.is_preseason)
    return 0


def _avg_pts(games: list[_GameRecord], attr: str, pts_fill: float) -> float:
    vals = [getattr(g, attr) for g in games if getattr(g, attr) is not None]
    if not vals:
        return pts_fill
    return float(sum(vals) / len(vals))


def _margin_avg(games: list[_GameRecord]) -> float:
    margins = [
        float(g.pts_for - g.pts_against)
        for g in games
        if g.pts_for is not None and g.pts_against is not None
    ]
    if not margins:
        return 0.0
    return sum(margins) / len(margins)


def _divisional_flag(row) -> int:
    if hasattr(row, "divisional") and pd.notna(row.divisional):
        return int(row.divisional)
    return is_divisional(_team_key(row, "home"), _team_key(row, "away"))


def _rest_days(row, side: str, rest_fill: float) -> float:
    col = f"{side}_rest_days"
    if hasattr(row, col) and pd.notna(getattr(row, col)):
        return float(getattr(row, col))
    return rest_fill


def rest_from_last_game(
    last_date: pd.Timestamp | None,
    game_date: pd.Timestamp,
    rest_fill: float,
) -> float:
    if last_date is None:
        return rest_fill
    gap = int((game_date - last_date).days)
    if 1 <= gap <= MAX_REST_GAP_DAYS:
        return float(gap)
    return rest_fill


def _row_features(
    row,
    tracker: _TeamTracker,
    rest_fill: float,
    *,
    pts_fill: float = DEFAULT_PTS_FILL,
    include_scoring: bool = False,
) -> dict[str, float | str | int]:
    game_date = pd.to_datetime(row.date)
    season = int(row.season)
    home_team = _team_key(row, "home")
    away_team = _team_key(row, "away")
    home_prior = tracker.games_before(home_team, game_date)
    away_prior = tracker.games_before(away_team, game_date)
    home_season_g = _season_games(home_prior, season)
    away_season_g = _season_games(away_prior, season)

    home_rest = _rest_days(row, "home", rest_fill)
    away_rest = _rest_days(row, "away", rest_fill)
    if not hasattr(row, "home_rest_days") or pd.isna(getattr(row, "home_rest_days", None)):
        home_rest = rest_from_last_game(
            tracker.last_game_date(home_team, game_date), game_date, rest_fill
        )
    if not hasattr(row, "away_rest_days") or pd.isna(getattr(row, "away_rest_days", None)):
        away_rest = rest_from_last_game(
            tracker.last_game_date(away_team, game_date), game_date, rest_fill
        )

    elo_home = float(getattr(row, "elo_home_pre", 1500.0) or 1500.0)
    elo_away = float(getattr(row, "elo_away_pre", 1500.0) or 1500.0)
    home_field = 0 if _neutral_site(row) else 1
    raw_week = getattr(row, "week", 0)
    week = 0 if raw_week is None or (isinstance(raw_week, float) and pd.isna(raw_week)) else int(raw_week or 0)
    pre = _is_preseason(row)
    week_norm = (week / 4.0) if pre and week else (week / 18.0 if week else 0.0)
    home_pf = _avg_pts(home_season_g, "pts_for", pts_fill) if home_season_g else pts_fill
    away_pf = _avg_pts(away_season_g, "pts_for", pts_fill) if away_season_g else pts_fill
    home_pa = _avg_pts(home_season_g, "pts_against", pts_fill) if home_season_g else pts_fill
    away_pa = _avg_pts(away_season_g, "pts_against", pts_fill) if away_season_g else pts_fill
    home_margin = _margin_avg(home_season_g)
    away_margin = _margin_avg(away_season_g)

    feats: dict[str, float | str | int] = {
        "game_id": str(row.game_id),
        "date": game_date,
        "home_team": str(getattr(row, "home_team", home_team)),
        "away_team": str(getattr(row, "away_team", away_team)),
        "home_team_abbr": home_team,
        "away_team_abbr": away_team,
        "season": season,
        "home_rest_days": home_rest,
        "away_rest_days": away_rest,
        "rest_diff": home_rest - away_rest,
        "home_season_win_pct": (
            _win_pct(home_season_g) if home_season_g else NEUTRAL_SEASON_WIN_PCT
        ),
        "away_season_win_pct": (
            _win_pct(away_season_g) if away_season_g else NEUTRAL_SEASON_WIN_PCT
        ),
        "home_field": home_field,
        "divisional": _divisional_flag(row),
        "is_preseason": pre,
        "game_type": "preseason" if pre else "regular",
        "week": week,
        "elo_home_pre": elo_home,
        "elo_away_pre": elo_away,
        "elo_diff": elo_home - elo_away,
        "home_l5_win_pct": _last_n_win_pct(home_prior, 5),
        "away_l5_win_pct": _last_n_win_pct(away_prior, 5),
        "home_l5_margin": _last_n_margin(home_prior, 5),
        "away_l5_margin": _last_n_margin(away_prior, 5),
        "home_streak": _streak(home_prior),
        "away_streak": _streak(away_prior),
        "home_home_win_pct": _split_win_pct(home_season_g, is_home=1),
        "away_road_win_pct": _split_win_pct(away_season_g, is_home=0),
        "short_rest_home": int(home_rest <= 5),
        "short_rest_away": int(away_rest <= 5),
        "bye_home": int(home_rest >= 13),
        "bye_away": int(away_rest >= 13),
        "week_norm": week_norm,
        "margin_diff": home_margin - away_margin,
        "pyth_diff": _pythagorean(home_season_g, pts_fill) - _pythagorean(away_season_g, pts_fill),
        "travel_miles": travel_miles(away_team, home_team, neutral=home_field == 0),
        "home_season_pts_for": home_pf,
        "away_season_pts_for": away_pf,
        "home_season_pts_against": home_pa,
        "away_season_pts_against": away_pa,
        "home_season_margin_avg": home_margin,
        "away_season_margin_avg": away_margin,
    }
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
                _team_key(row, "home"),
                _team_key(row, "away"),
                int(row.home_win),
                int(row.season),
                home_score=home_score,
                away_score=away_score,
            )

    out = pd.DataFrame(rows)
    if attach_elo:
        from app.models.nfl_baseline import attach_elo_features

        # Walk ratings in date order. Rows without home_win (live slate) keep
        # the current rating and do not update. Never use attach_elo_for_slate
        # on a history+slate frame — min_date would be 2019 and every team
        # would reset to 1500 (≈58/42 home-field).
        out = attach_elo_features(out)
        out["elo_diff"] = out["elo_home_pre"] - out["elo_away_pre"]
    return out


def _train_rest_fill(games: pd.DataFrame) -> float:
    train = games[games["season"].isin([2019, 2020, 2021, 2022, 2023, 2024])]
    if train.empty or "home_rest_days" not in train.columns:
        return DEFAULT_REST_FILL
    rest_fill = float(
        pd.concat([train["home_rest_days"], train["away_rest_days"]]).median()
    )
    if pd.isna(rest_fill):
        return DEFAULT_REST_FILL
    return rest_fill


def build_features_for_history(games_df: pd.DataFrame | None = None) -> pd.DataFrame:
    from app.models.nfl_baseline import load_games

    games = games_df if games_df is not None else load_games()
    rest_fill = _train_rest_fill(games)
    return build_features(games, rest_fill=rest_fill)


def build_features_for_slate(
    slate_rows: pd.DataFrame,
    history_df: pd.DataFrame | None = None,
    *,
    rest_fill: float = DEFAULT_REST_FILL,
    include_scoring: bool = False,
) -> pd.DataFrame:
    from app.models.nfl_baseline import load_games

    hist = history_df if history_df is not None else load_games()
    hist = hist[hist["home_win"].notna()].copy()
    hist["date"] = pd.to_datetime(hist["date"])
    fill = _train_rest_fill(hist) if rest_fill == DEFAULT_REST_FILL else rest_fill

    slate = slate_rows.copy()
    slate["date"] = pd.to_datetime(slate["date"])
    if "season" not in slate.columns:
        slate["season"] = slate["date"].apply(
            lambda d: d.year if pd.Timestamp(d).month >= 8 else pd.Timestamp(d).year - 1
        )
    for col in ("home_team_abbr", "away_team_abbr"):
        if col in slate.columns:
            slate[col] = slate[col].map(normalize_abbr)
    if "divisional" not in slate.columns:
        slate["divisional"] = [
            is_divisional(h, a)
            for h, a in zip(
                slate.get("home_team_abbr", pd.Series([""] * len(slate))),
                slate.get("away_team_abbr", pd.Series([""] * len(slate))),
            )
        ]
    if "neutral_site" not in slate.columns:
        slate["neutral_site"] = 0
    if "game_type" not in slate.columns:
        if "is_preseason" in slate.columns:
            slate["game_type"] = slate["is_preseason"].map(
                lambda v: "preseason" if int(v or 0) == 1 else "regular"
            )
        else:
            slate["game_type"] = "regular"

    slate_ids = set(slate["game_id"].astype(str))
    slate_min_date = pd.to_datetime(slate["date"]).min()
    hist = hist[~hist["game_id"].astype(str).isin(slate_ids)].copy()
    hist_before = hist[hist["date"] < slate_min_date].copy()
    tracker = build_team_tracker_from_history(hist_before)

    # Compute rest from last completed game when slate rows lack rest columns.
    home_rest: list[float] = []
    away_rest: list[float] = []
    for row in slate.itertuples(index=False):
        game_date = pd.to_datetime(row.date)
        home_key = _team_key(row, "home")
        away_key = _team_key(row, "away")
        if hasattr(row, "home_rest_days") and pd.notna(getattr(row, "home_rest_days")):
            home_rest.append(float(row.home_rest_days))
        else:
            home_rest.append(
                rest_from_last_game(tracker.last_game_date(home_key, game_date), game_date, fill)
            )
        if hasattr(row, "away_rest_days") and pd.notna(getattr(row, "away_rest_days")):
            away_rest.append(float(row.away_rest_days))
        else:
            away_rest.append(
                rest_from_last_game(tracker.last_game_date(away_key, game_date), game_date, fill)
            )
    slate = slate.copy()
    slate["home_rest_days"] = home_rest
    slate["away_rest_days"] = away_rest

    from app.models.nfl_baseline import attach_elo_for_slate

    full = build_features(
        slate,
        rest_fill=fill,
        tracker=tracker,
        update_state=False,
        attach_elo=False,
        include_scoring=include_scoring,
    )
    full = attach_elo_for_slate(full, history=hist_before)
    full["elo_diff"] = full["elo_home_pre"] - full["elo_away_pre"]
    return (
        full[full["game_id"].astype(str).isin(slate_ids)]
        .drop_duplicates(subset=["game_id"], keep="last")
        .copy()
    )


def build_margin_features_for_history(games_df: pd.DataFrame | None = None) -> pd.DataFrame:
    from app.models.nfl_baseline import load_games

    games = games_df if games_df is not None else load_games()
    rest_fill = _train_rest_fill(games)
    return build_features(games, rest_fill=rest_fill, include_scoring=True)


def build_margin_features_for_slate(
    slate_rows: pd.DataFrame,
    history_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    return build_features_for_slate(slate_rows, history_df, include_scoring=True)


def build_totals_features_for_history(games_df: pd.DataFrame | None = None) -> pd.DataFrame:
    return build_margin_features_for_history(games_df)


def build_totals_features_for_slate(
    slate_rows: pd.DataFrame,
    history_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    return build_margin_features_for_slate(slate_rows, history_df)
