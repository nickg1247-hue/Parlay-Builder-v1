"""CFB pregame features — Elo, season win %, rest, form, conference; no same-day leakage."""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass

import pandas as pd

from app.odds.cfb_team_aliases import normalize_team_name

NEUTRAL_SEASON_WIN_PCT = 0.5
DEFAULT_REST_FILL = 7.0
DEFAULT_PTS_FILL = 28.0
LAST_N_FORM = 5

FEATURE_COLUMNS_V1 = [
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

FEATURE_COLUMNS_V2 = [
    "elo_diff",
    "home_season_win_pct",
    "away_season_win_pct",
    "home_rest_days",
    "away_rest_days",
    "rest_diff",
    "neutral_site",
    "home_field_active",
    "home_last5_win_pct",
    "away_last5_win_pct",
    "last5_win_pct_diff",
    "home_home_win_pct",
    "conf_win_pct_diff",
    "home_b2b",
    "away_b2b",
]

FEATURE_COLUMNS_V3 = FEATURE_COLUMNS_V2 + [
    "sp_plus_diff",
    "sp_offense_diff",
    "sp_defense_diff",
]

FEATURE_COLUMNS_V4 = FEATURE_COLUMNS_V3 + [
    "talent_diff",
    "returning_pct_diff",
    "returning_pass_pct_diff",
    "prior_fpi_diff",
    "coach_change_home",
    "coach_change_away",
    "srs_diff",
    "program_home_margin",
    "matchup_tier_diff",
    "is_fcs_away",
    "conference_game",
    "week_norm",
    "prior_weight",
    "blended_quality_diff",
]

FEATURE_COLUMNS = list(FEATURE_COLUMNS_V4)

P4_CONF_KEYS = (
    "sec",
    "southeastern",
    "big ten",
    "big 12",
    "acc",
    "atlantic coast",
    "pac-12",
    "pac 12",
)
G5_CONF_KEYS = (
    "american athletic",
    "aac",
    "sun belt",
    "mid-american",
    "mac",
    "mountain west",
    "conference usa",
    "cusa",
)
SRS_LEARN_RATE = 0.20
SRS_MARGIN_CAP = 28.0


def conference_tier(name: str | None) -> int:
    """3 = P4 / Notre Dame, 2 = G5 / FBS independent, 1 = FCS / unknown."""
    raw = str(name or "").strip().lower()
    if not raw:
        return 1
    if "notre dame" in raw:
        return 3
    if any(key in raw for key in P4_CONF_KEYS):
        return 3
    if "independent" in raw:
        return 2
    if any(key in raw for key in G5_CONF_KEYS):
        return 2
    return 1


def prior_blend_weight(week: int) -> float:
    """Weeks 1–3 mostly priors; fade to 30% by week 8. Never zero."""
    wk = max(1, int(week or 1))
    if wk <= 3:
        return 0.70
    if wk >= 8:
        return 0.30
    return 0.70 - (wk - 3) * (0.40 / 5.0)

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
    "neutral_site",
    "last5_win_pct_diff",
]

TOTALS_FEATURE_COLUMNS = list(MARGIN_FEATURE_COLUMNS)


@dataclass
class _GameRecord:
    date: pd.Timestamp
    team: str
    win: int
    season: int
    was_home: bool
    pts_for: int | None = None
    pts_against: int | None = None


class _ConferenceTracker:
    def __init__(self) -> None:
        self._wins: dict[tuple[int, str], int] = defaultdict(int)
        self._games: dict[tuple[int, str], int] = defaultdict(int)

    def win_pct_before(self, season: int, conference: str | None) -> float:
        if conference is None or (isinstance(conference, float) and pd.isna(conference)):
            return NEUTRAL_SEASON_WIN_PCT
        conf = str(conference).strip()
        if not conf:
            return NEUTRAL_SEASON_WIN_PCT
        key = (season, conf)
        games = self._games[key]
        if games == 0:
            return NEUTRAL_SEASON_WIN_PCT
        return self._wins[key] / games

    def update(
        self,
        season: int,
        home_conf: str,
        away_conf: str,
        home_win: int,
    ) -> None:
        for conf, won in ((home_conf, int(home_win)), (away_conf, 1 - int(home_win))):
            c = str(conf or "").strip()
            if not c:
                continue
            key = (season, c)
            self._wins[key] += int(won)
            self._games[key] += 1


class _SrsTracker:
    def __init__(self, learn_rate: float = SRS_LEARN_RATE, cap: float = SRS_MARGIN_CAP) -> None:
        self.ratings: dict[str, float] = {}
        self.learn_rate = learn_rate
        self.cap = cap

    def pre(self, team: str) -> float:
        return float(self.ratings.get(normalize_team_name(team), 0.0))

    def update(
        self,
        home_team: str,
        away_team: str,
        home_score: int | None,
        away_score: int | None,
    ) -> None:
        if home_score is None or away_score is None:
            return
        home = normalize_team_name(home_team)
        away = normalize_team_name(away_team)
        home_pre = self.pre(home)
        away_pre = self.pre(away)
        margin = max(-self.cap, min(self.cap, float(home_score - away_score)))
        resid = margin - (home_pre - away_pre)
        self.ratings[home] = home_pre + self.learn_rate * resid
        self.ratings[away] = away_pre - self.learn_rate * resid


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
        for team, win, pts_for, pts_against, was_home in (
            (home_team, int(home_win), home_score, away_score, True),
            (away_team, 1 - int(home_win), away_score, home_score, False),
        ):
            self._records[team].append(
                _GameRecord(game_date, team, win, season, was_home, pts_for, pts_against)
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


def _last_n_win_pct(games: list[_GameRecord], n: int = LAST_N_FORM) -> float:
    if not games:
        return NEUTRAL_SEASON_WIN_PCT
    recent = games[-n:]
    return sum(g.win for g in recent) / len(recent)


def _home_win_pct(games: list[_GameRecord]) -> float:
    home_games = [g for g in games if g.was_home]
    if not home_games:
        return NEUTRAL_SEASON_WIN_PCT
    return sum(g.win for g in home_games) / len(home_games)


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


def _home_program_margin(games: list[_GameRecord]) -> float:
    return _margin_avg([g for g in games if g.was_home])


def _neutral_site(row) -> int:
    if hasattr(row, "neutral_site") and pd.notna(row.neutral_site):
        return int(row.neutral_site)
    return 0


def _game_week(row) -> int:
    if hasattr(row, "week") and pd.notna(getattr(row, "week", None)):
        try:
            wk = int(row.week)
            if wk > 0:
                return wk
        except (TypeError, ValueError):
            pass
    try:
        from app.ingest.cfb_sp_plus import resolve_game_week

        season = int(row.season)
        game_date = str(getattr(row, "date", ""))[:10]
        return resolve_game_week(game_date, season)
    except Exception:
        return 1


def _row_features(
    row,
    tracker: _TeamTracker,
    conf_tracker: _ConferenceTracker,
    rest_fill: float,
    *,
    pts_fill: float = DEFAULT_PTS_FILL,
    include_scoring: bool = False,
    sp_lookup: dict | None = None,
    priors_store: object | None = None,
    srs_tracker: _SrsTracker | None = None,
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

    neutral = _neutral_site(row)
    home_field_active = 0 if neutral else 1

    home_last5 = _last_n_win_pct(home_prior)
    away_last5 = _last_n_win_pct(away_prior)
    home_home_pct = _home_win_pct(home_prior)

    home_conf = getattr(row, "home_conference", "") or ""
    away_conf = getattr(row, "away_conference", "") or ""
    home_conf_pct = conf_tracker.win_pct_before(season, home_conf)
    away_conf_pct = conf_tracker.win_pct_before(season, away_conf)
    conf_diff = home_conf_pct - away_conf_pct

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
        "neutral_site": neutral,
        "home_field_active": home_field_active,
        "home_field": home_field_active,
        "home_last5_win_pct": home_last5,
        "away_last5_win_pct": away_last5,
        "last5_win_pct_diff": home_last5 - away_last5,
        "home_home_win_pct": home_home_pct,
        "conf_win_pct_diff": conf_diff,
        "elo_home_pre": elo_home,
        "elo_away_pre": elo_away,
        "elo_diff": elo_home - elo_away,
    }

    if sp_lookup is not None:
        from app.ingest.cfb_sp_plus import sp_plus_diffs_for_game

        game_week = _game_week(row)
        sp_diff, sp_off_diff, sp_def_diff = sp_plus_diffs_for_game(
            season=season,
            game_week=game_week,
            home_team=home_team,
            away_team=away_team,
            lookup=sp_lookup,
        )
        feats["sp_plus_diff"] = sp_diff
        feats["sp_offense_diff"] = sp_off_diff
        feats["sp_defense_diff"] = sp_def_diff
    else:
        feats["sp_plus_diff"] = 0.0
        feats["sp_offense_diff"] = 0.0
        feats["sp_defense_diff"] = 0.0

    game_week = _game_week(row)
    weight = prior_blend_weight(game_week)
    home_tier = conference_tier(home_conf)
    away_tier = conference_tier(away_conf)
    srs_home = srs_tracker.pre(home_team) if srs_tracker is not None else 0.0
    srs_away = srs_tracker.pre(away_team) if srs_tracker is not None else 0.0
    srs_diff = srs_home - srs_away
    prior_diffs = {
        "talent_diff": 0.0,
        "returning_pct_diff": 0.0,
        "returning_pass_pct_diff": 0.0,
        "prior_fpi_diff": 0.0,
        "coach_change_home": 0.0,
        "coach_change_away": 0.0,
    }
    if priors_store is not None:
        from app.ingest.cfb_priors import prior_feature_diffs

        prior_diffs = prior_feature_diffs(
            season=season,
            home_team=home_team,
            away_team=away_team,
            store=priors_store,
        )
    prior_points = (
        0.40 * float(feats.get("sp_plus_diff") or 0.0)
        + 0.25 * float(prior_diffs["prior_fpi_diff"])
        + 0.15 * (float(prior_diffs["talent_diff"]) / 50.0)
        + 0.12 * (float(prior_diffs["returning_pct_diff"]) * 10.0)
        + 0.08 * (float(prior_diffs["returning_pass_pct_diff"]) * 10.0)
    )
    in_season_points = 0.60 * (float(feats["elo_diff"]) / 25.0) + 0.40 * srs_diff
    feats.update(prior_diffs)
    feats["srs_diff"] = srs_diff
    feats["program_home_margin"] = _home_program_margin(home_prior)
    feats["matchup_tier_diff"] = float(home_tier - away_tier)
    feats["is_fcs_away"] = 1.0 if away_tier == 1 else 0.0
    feats["conference_game"] = (
        int(row.conference_game)
        if hasattr(row, "conference_game") and pd.notna(row.conference_game)
        else 0
    )
    feats["week_norm"] = min(1.0, max(0.0, game_week / 15.0))
    feats["prior_weight"] = weight
    feats["blended_quality_diff"] = weight * prior_points + (1.0 - weight) * in_season_points

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
    conf_tracker: _ConferenceTracker | None = None,
    attach_elo: bool = True,
    include_scoring: bool = False,
    sp_lookup: dict | None = None,
    priors_store: object | None = None,
    srs_tracker: _SrsTracker | None = None,
) -> pd.DataFrame:
    df = games_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["date", "game_id"]).reset_index(drop=True)
    state = tracker if tracker is not None else _TeamTracker()
    conf_state = conf_tracker if conf_tracker is not None else _ConferenceTracker()
    srs_state = srs_tracker if srs_tracker is not None else _SrsTracker()
    rows: list[dict] = []

    for row in df.itertuples(index=False):
        feats = _row_features(
            row,
            state,
            conf_state,
            rest_fill,
            pts_fill=pts_fill,
            include_scoring=include_scoring,
            sp_lookup=sp_lookup,
            priors_store=priors_store,
            srs_tracker=srs_state,
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
            conf_state.update(
                int(row.season),
                str(getattr(row, "home_conference", "") or ""),
                str(getattr(row, "away_conference", "") or ""),
                int(row.home_win),
            )
            srs_state.update(
                normalize_team_name(str(row.home_team)),
                normalize_team_name(str(row.away_team)),
                home_score,
                away_score,
            )

    out = pd.DataFrame(rows)
    if attach_elo and "home_win" in out.columns and out["home_win"].notna().all():
        from app.models.cfb_baseline import attach_elo_features

        out = attach_elo_features(out, games_df=df)
        out["elo_diff"] = out["elo_home_pre"] - out["elo_away_pre"]
    elif attach_elo:
        from app.models.cfb_baseline import attach_elo_for_slate

        out = attach_elo_for_slate(out, games_df=df)
        out["elo_diff"] = out["elo_home_pre"] - out["elo_away_pre"]
    return out


def _ensure_v2_game_columns(games: pd.DataFrame) -> pd.DataFrame:
    out = games.copy()
    for col, default in (
        ("neutral_site", 0),
        ("conference_game", 0),
        ("home_conference", ""),
        ("away_conference", ""),
        ("week", 0),
    ):
        if col not in out.columns:
            out[col] = default
        else:
            out[col] = out[col].fillna(default)
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


def _load_priors_store(games: pd.DataFrame):
    from app.ingest.cfb_priors import load_priors_store

    seasons = tuple(sorted(int(s) for s in games["season"].unique()))
    return load_priors_store(seasons)


def build_features_for_history(
    games_df: pd.DataFrame | None = None,
    *,
    sp_lookup: dict | None = None,
    priors_store: object | None = None,
) -> pd.DataFrame:
    from app.models.cfb_baseline import load_games

    games = _ensure_v2_game_columns(games_df if games_df is not None else load_games())
    rest_fill, _ = _train_imputation_fills(games)
    lookup = sp_lookup
    if lookup is None:
        from app.ingest.cfb_sp_plus import load_sp_plus_lookup

        lookup = load_sp_plus_lookup(tuple(sorted(games["season"].unique())))
    priors = priors_store if priors_store is not None else _load_priors_store(games)
    return build_features(games, rest_fill=rest_fill, sp_lookup=lookup, priors_store=priors)


def _build_slate_features(
    slate_rows: pd.DataFrame,
    history_df: pd.DataFrame | None,
    *,
    include_scoring: bool,
) -> pd.DataFrame:
    from app.models.cfb_baseline import load_games

    hist = _ensure_v2_game_columns(history_df if history_df is not None else load_games())
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
    for col, default in (
        ("neutral_site", 0),
        ("conference_game", 0),
        ("home_conference", ""),
        ("away_conference", ""),
        ("week", 0),
    ):
        if col not in slate.columns:
            slate[col] = default

    slate_ids = set(slate["game_id"].astype(str))
    slate_min_date = pd.to_datetime(slate["date"]).min()
    hist = hist[~hist["game_id"].astype(str).isin(slate_ids)].copy()
    hist_before = hist[hist["date"] < slate_min_date].copy()
    tracker = build_team_tracker_from_history(hist_before)
    conf_tracker = _ConferenceTracker()
    for row in hist_before.sort_values(["date", "game_id"]).itertuples(index=False):
        conf_tracker.update(
            int(row.season),
            str(getattr(row, "home_conference", "") or ""),
            str(getattr(row, "away_conference", "") or ""),
            int(row.home_win),
        )
    combined = pd.concat([hist_before, slate], ignore_index=True, sort=False)
    combined["date"] = pd.to_datetime(combined["date"])
    combined = combined.sort_values(["date", "game_id"]).reset_index(drop=True)
    from app.ingest.cfb_sp_plus import load_sp_plus_lookup

    sp_lookup = load_sp_plus_lookup(tuple(sorted(combined["season"].unique())))
    priors_store = _load_priors_store(combined)
    full = build_features(
        combined,
        rest_fill=rest_fill,
        pts_fill=pts_fill,
        tracker=tracker,
        conf_tracker=conf_tracker,
        attach_elo=True,
        include_scoring=include_scoring,
        sp_lookup=sp_lookup,
        priors_store=priors_store,
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
    del rest_fill
    return _build_slate_features(slate_rows, history_df, include_scoring=False)


def build_margin_features_for_history(games_df: pd.DataFrame | None = None) -> pd.DataFrame:
    from app.models.cfb_baseline import load_games

    games = _ensure_v2_game_columns(games_df if games_df is not None else load_games())
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
