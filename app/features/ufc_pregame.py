"""UFC pregame features — fighter Elo, form, rest; no same-day leakage."""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd

from app.odds.ufc_fighter_aliases import fighter_match_key, normalize_fighter_name

NEUTRAL_WIN_PCT = 0.5
DEFAULT_REST_FILL = 90.0
LAST_N_FORM = 5

FEATURE_COLUMNS = [
    "elo_diff",
    "home_career_win_pct",
    "away_career_win_pct",
    "home_rest_days",
    "away_rest_days",
    "rest_diff",
    "home_last5_win_pct",
    "away_last5_win_pct",
    "last5_win_pct_diff",
    "home_b2b",
    "away_b2b",
]

STAT_ROLLING_N = 5
STAT_ROLLING_COLUMNS = [
    "home_sig_strikes_landed_avg",
    "away_sig_strikes_landed_avg",
    "sig_strikes_landed_diff",
    "home_takedowns_landed_avg",
    "away_takedowns_landed_avg",
    "takedowns_landed_diff",
    "home_control_seconds_avg",
    "away_control_seconds_avg",
    "control_seconds_diff",
    "stats_available",
]


@dataclass
class _FightRecord:
    date: pd.Timestamp
    fighter: str
    win: int


@dataclass
class _FighterStatSample:
    date: pd.Timestamp
    sig_strikes_landed: float
    takedowns_landed: float
    control_seconds: float


class _FighterStatsTracker:
    def __init__(self) -> None:
        self._samples: dict[str, list[_FighterStatSample]] = defaultdict(list)
        self._dates: dict[str, list[pd.Timestamp]] = defaultdict(list)

    def samples_before(self, fighter: str, before: pd.Timestamp) -> list[_FighterStatSample]:
        dates = self._dates.get(fighter)
        if not dates:
            return []
        idx = bisect_left(dates, before)
        return self._samples[fighter][:idx]

    def update_fight(
        self,
        fight_date: pd.Timestamp,
        home_team: str,
        away_team: str,
        *,
        home_sig_strikes_landed: float | None,
        away_sig_strikes_landed: float | None,
        home_takedowns_landed: float | None,
        away_takedowns_landed: float | None,
        home_control_seconds: float | None,
        away_control_seconds: float | None,
    ) -> None:
        pairs = (
            (home_team, home_sig_strikes_landed, home_takedowns_landed, home_control_seconds),
            (away_team, away_sig_strikes_landed, away_takedowns_landed, away_control_seconds),
        )
        for fighter, ssl, td, ctrl in pairs:
            if ssl is None and td is None and ctrl is None:
                continue
            sample = _FighterStatSample(
                date=fight_date,
                sig_strikes_landed=float(ssl or 0.0),
                takedowns_landed=float(td or 0.0),
                control_seconds=float(ctrl or 0.0),
            )
            self._samples[fighter].append(sample)
            self._dates[fighter].append(fight_date)


def build_fighter_stats_tracker_from_history(
    stats_df: pd.DataFrame,
) -> _FighterStatsTracker:
    tracker = _FighterStatsTracker()
    if stats_df is None or stats_df.empty:
        return tracker
    df = stats_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    for row in df.sort_values(["date", "fight_id"]).itertuples(index=False):
        tracker.update_fight(
            pd.to_datetime(row.date),
            normalize_fighter_name(str(row.home_team)),
            normalize_fighter_name(str(row.away_team)),
            home_sig_strikes_landed=_optional_float(getattr(row, "home_sig_strikes_landed", None)),
            away_sig_strikes_landed=_optional_float(getattr(row, "away_sig_strikes_landed", None)),
            home_takedowns_landed=_optional_float(getattr(row, "home_takedowns_landed", None)),
            away_takedowns_landed=_optional_float(getattr(row, "away_takedowns_landed", None)),
            home_control_seconds=_optional_float(getattr(row, "home_control_seconds", None)),
            away_control_seconds=_optional_float(getattr(row, "away_control_seconds", None)),
        )
    return tracker


def _optional_float(val: Any) -> float | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if pd.isna(f):
        return None
    return f


def _rolling_stat_avg(
    samples: list[_FighterStatSample],
    attr: str,
    n: int = STAT_ROLLING_N,
) -> float | None:
    if not samples:
        return None
    recent = samples[-n:]
    vals = [getattr(s, attr) for s in recent]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _attach_rolling_stats(
    feats: dict[str, float | str | int],
    *,
    home_team: str,
    away_team: str,
    game_date: pd.Timestamp,
    stats_tracker: _FighterStatsTracker | None,
) -> None:
    if stats_tracker is None:
        for col in STAT_ROLLING_COLUMNS:
            feats[col] = 0 if col == "stats_available" else None
        return

    home_prior = stats_tracker.samples_before(home_team, game_date)
    away_prior = stats_tracker.samples_before(away_team, game_date)
    home_ssl = _rolling_stat_avg(home_prior, "sig_strikes_landed")
    away_ssl = _rolling_stat_avg(away_prior, "sig_strikes_landed")
    home_td = _rolling_stat_avg(home_prior, "takedowns_landed")
    away_td = _rolling_stat_avg(away_prior, "takedowns_landed")
    home_ctrl = _rolling_stat_avg(home_prior, "control_seconds")
    away_ctrl = _rolling_stat_avg(away_prior, "control_seconds")

    has_stats = any(v is not None for v in (home_ssl, away_ssl, home_td, away_td, home_ctrl, away_ctrl))
    feats["home_sig_strikes_landed_avg"] = home_ssl
    feats["away_sig_strikes_landed_avg"] = away_ssl
    feats["sig_strikes_landed_diff"] = (
        (home_ssl - away_ssl) if home_ssl is not None and away_ssl is not None else None
    )
    feats["home_takedowns_landed_avg"] = home_td
    feats["away_takedowns_landed_avg"] = away_td
    feats["takedowns_landed_diff"] = (
        (home_td - away_td) if home_td is not None and away_td is not None else None
    )
    feats["home_control_seconds_avg"] = home_ctrl
    feats["away_control_seconds_avg"] = away_ctrl
    feats["control_seconds_diff"] = (
        (home_ctrl - away_ctrl) if home_ctrl is not None and away_ctrl is not None else None
    )
    feats["stats_available"] = int(has_stats)


class _FighterTracker:
    def __init__(self) -> None:
        self._records: dict[str, list[_FightRecord]] = defaultdict(list)
        self._dates: dict[str, list[pd.Timestamp]] = defaultdict(list)

    def fights_before(self, fighter: str, before: pd.Timestamp) -> list[_FightRecord]:
        dates = self._dates.get(fighter)
        if not dates:
            return []
        idx = bisect_left(dates, before)
        return self._records[fighter][:idx]

    def update(
        self,
        fight_date: pd.Timestamp,
        home_team: str,
        away_team: str,
        home_win: int,
    ) -> None:
        for fighter, win in ((home_team, int(home_win)), (away_team, 1 - int(home_win))):
            self._records[fighter].append(_FightRecord(fight_date, fighter, win))
            self._dates[fighter].append(fight_date)


def build_fighter_tracker_from_history(fights_df: pd.DataFrame) -> _FighterTracker:
    tracker = _FighterTracker()
    df = fights_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    for row in df.sort_values(["date", "fight_id"]).itertuples(index=False):
        if pd.isna(getattr(row, "home_win", None)):
            continue
        tracker.update(
            pd.to_datetime(row.date),
            normalize_fighter_name(str(row.home_team)),
            normalize_fighter_name(str(row.away_team)),
            int(row.home_win),
        )
    return tracker


def _win_pct(games: list[_FightRecord]) -> float:
    if not games:
        return NEUTRAL_WIN_PCT
    return sum(g.win for g in games) / len(games)


def _last_n_win_pct(games: list[_FightRecord], n: int = LAST_N_FORM) -> float:
    if not games:
        return NEUTRAL_WIN_PCT
    recent = games[-n:]
    return sum(g.win for g in recent) / len(recent)


def _load_fight_stats_df() -> pd.DataFrame | None:
    try:
        from app.ingest.ufc_fight_stats import load_fight_stats

        stats = load_fight_stats()
        return stats if not stats.empty else None
    except (ImportError, OSError, ValueError):
        return None


def _row_features(
    row,
    tracker: _FighterTracker,
    rest_fill: float,
    *,
    stats_tracker: _FighterStatsTracker | None = None,
) -> dict[str, float | str | int]:
    game_date = pd.to_datetime(row.date)
    home_team = normalize_fighter_name(str(row.home_team))
    away_team = normalize_fighter_name(str(row.away_team))
    home_prior = tracker.fights_before(home_team, game_date)
    away_prior = tracker.fights_before(away_team, game_date)

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
        int(row.home_b2b) if hasattr(row, "home_b2b") and pd.notna(row.home_b2b) else 0
    )
    away_b2b = (
        int(row.away_b2b) if hasattr(row, "away_b2b") and pd.notna(row.away_b2b) else 0
    )

    home_last5 = _last_n_win_pct(home_prior)
    away_last5 = _last_n_win_pct(away_prior)
    elo_home = float(getattr(row, "elo_home_pre", 1500.0) or 1500.0)
    elo_away = float(getattr(row, "elo_away_pre", 1500.0) or 1500.0)

    feats: dict[str, float | str | int] = {
        "fight_id": str(row.fight_id),
        "date": game_date,
        "home_team": home_team,
        "away_team": away_team,
        "season": int(row.season),
        "home_rest_days": home_rest,
        "away_rest_days": away_rest,
        "home_b2b": home_b2b,
        "away_b2b": away_b2b,
        "home_career_win_pct": _win_pct(home_prior),
        "away_career_win_pct": _win_pct(away_prior),
        "rest_diff": home_rest - away_rest,
        "home_last5_win_pct": home_last5,
        "away_last5_win_pct": away_last5,
        "last5_win_pct_diff": home_last5 - away_last5,
        "elo_home_pre": elo_home,
        "elo_away_pre": elo_away,
        "elo_diff": elo_home - elo_away,
    }
    _attach_rolling_stats(
        feats,
        home_team=home_team,
        away_team=away_team,
        game_date=game_date,
        stats_tracker=stats_tracker,
    )
    return feats


def build_features(
    fights_df: pd.DataFrame,
    *,
    rest_fill: float = DEFAULT_REST_FILL,
    update_state: bool = True,
    tracker: _FighterTracker | None = None,
    stats_tracker: _FighterStatsTracker | None = None,
    fight_stats_df: pd.DataFrame | None = None,
    attach_elo: bool = True,
) -> pd.DataFrame:
    df = fights_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["date", "fight_id"]).reset_index(drop=True)
    state = tracker if tracker is not None else _FighterTracker()
    stats_state = stats_tracker if stats_tracker is not None else _FighterStatsTracker()
    stats_lookup: pd.DataFrame | None = None
    if fight_stats_df is not None and not fight_stats_df.empty:
        stats_lookup = fight_stats_df.copy()
        stats_lookup["fight_id"] = stats_lookup["fight_id"].astype(str)
    rows: list[dict] = []

    for row in df.itertuples(index=False):
        feats = _row_features(row, state, rest_fill, stats_tracker=stats_state)
        if hasattr(row, "home_win") and pd.notna(getattr(row, "home_win", None)):
            feats["home_win"] = int(row.home_win)
        rows.append(feats)
        if update_state and hasattr(row, "home_win") and pd.notna(row.home_win):
            state.update(
                pd.to_datetime(row.date),
                normalize_fighter_name(str(row.home_team)),
                normalize_fighter_name(str(row.away_team)),
                int(row.home_win),
            )
        if stats_lookup is not None:
            fid = str(row.fight_id)
            match = stats_lookup[stats_lookup["fight_id"] == fid]
            if not match.empty:
                srow = match.iloc[0]
                stats_state.update_fight(
                    pd.to_datetime(row.date),
                    normalize_fighter_name(str(row.home_team)),
                    normalize_fighter_name(str(row.away_team)),
                    home_sig_strikes_landed=_optional_float(srow.get("home_sig_strikes_landed")),
                    away_sig_strikes_landed=_optional_float(srow.get("away_sig_strikes_landed")),
                    home_takedowns_landed=_optional_float(srow.get("home_takedowns_landed")),
                    away_takedowns_landed=_optional_float(srow.get("away_takedowns_landed")),
                    home_control_seconds=_optional_float(srow.get("home_control_seconds")),
                    away_control_seconds=_optional_float(srow.get("away_control_seconds")),
                )

    out = pd.DataFrame(rows)
    if attach_elo:
        from app.models.ufc_baseline import attach_elo_features, attach_elo_for_slate

        if "home_win" in out.columns and out["home_win"].notna().all():
            out = attach_elo_features(out, fights_df=df)
        else:
            out = attach_elo_for_slate(out, fights_df=df)
        out["elo_diff"] = out["elo_home_pre"] - out["elo_away_pre"]
    return out


def _train_rest_fill(fights: pd.DataFrame) -> float:
    train = fights[fights["season"].isin([2021, 2022, 2023])]
    if train.empty:
        return DEFAULT_REST_FILL
    rest_fill = float(pd.concat([train["home_rest_days"], train["away_rest_days"]]).median())
    if pd.isna(rest_fill):
        return DEFAULT_REST_FILL
    return rest_fill


def build_features_for_history(fights_df: pd.DataFrame | None = None) -> pd.DataFrame:
    from app.models.ufc_baseline import load_fights

    fights = fights_df if fights_df is not None else load_fights()
    rest_fill = _train_rest_fill(fights)
    fight_stats = _load_fight_stats_df()
    return build_features(
        fights,
        rest_fill=rest_fill,
        fight_stats_df=fight_stats,
    )


def format_layoff_label(days: int | float | None) -> str:
    """Human-readable time since last bout (uncapped — for display only)."""
    if days is None or (isinstance(days, float) and pd.isna(days)):
        return "—"
    d = int(days)
    if d < 0:
        return "—"
    if d == 0:
        return "Same week"
    if d <= 7:
        return f"{d} days (quick turnaround)"
    if d < 60:
        return f"{d} days since last fight"
    if d < 365:
        months = max(1, round(d / 30))
        return f"{months} mo since last fight"
    years = d // 365
    rem = d % 365
    months = rem // 30
    if months >= 1:
        return f"{years}y {months}mo since last fight"
    return f"{years} year{'s' if years != 1 else ''} since last fight"


def fighter_layoff_days(fighter: str, as_of: date | pd.Timestamp) -> int | None:
    """Calendar days since fighter's last completed bout before as_of."""
    from app.models.ufc_baseline import load_fights

    name = normalize_fighter_name(fighter)
    if not name:
        return None
    target_key = fighter_match_key(name)
    as_of_ts = pd.Timestamp(as_of)

    try:
        fights = load_fights()
    except (FileNotFoundError, OSError):
        return None

    last_fight: pd.Timestamp | None = None
    for row in fights.itertuples(index=False):
        if pd.isna(getattr(row, "home_win", None)):
            continue
        fight_dt = pd.to_datetime(row.date)
        if fight_dt >= as_of_ts:
            continue
        home_key = fighter_match_key(normalize_fighter_name(str(row.home_team)))
        away_key = fighter_match_key(normalize_fighter_name(str(row.away_team)))
        if target_key not in (home_key, away_key):
            continue
        if last_fight is None or fight_dt > last_fight:
            last_fight = fight_dt

    if last_fight is None:
        return None
    return int((as_of_ts.normalize() - last_fight.normalize()).days)


def estimate_rounds_expected(
    *,
    totals_line: Any = None,
    model_prob_home: float | None = None,
    model_prob_away: float | None = None,
    is_title_fight: bool = False,
) -> dict[str, Any]:
    """Bettor-facing rounds expectation — book line first, else simple finish heuristic."""
    if totals_line is not None and not (isinstance(totals_line, float) and pd.isna(totals_line)):
        val = float(totals_line)
        return {
            "value": val,
            "label": f"{val:g}",
            "source": "book",
            "display": f"O/U {val:g} rounds",
        }

    p_home = float(model_prob_home) if model_prob_home is not None else 0.5
    p_away = float(model_prob_away) if model_prob_away is not None else 0.5
    favorite_prob = max(p_home, p_away)
    if is_title_fight:
        base = 3.5 if favorite_prob < 0.62 else 2.5
    elif favorite_prob >= 0.78:
        base = 1.5
    elif favorite_prob >= 0.65:
        base = 2.0
    else:
        base = 2.5

    return {
        "value": base,
        "label": f"~{base:g}",
        "source": "estimate",
        "display": f"~{base:g} rounds",
    }


def build_features_for_slate(
    slate_rows: pd.DataFrame,
    history_df: pd.DataFrame | None = None,
    *,
    rest_fill: float = DEFAULT_REST_FILL,
) -> pd.DataFrame:
    from app.models.ufc_baseline import load_fights

    hist = history_df if history_df is not None else load_fights()
    hist = hist[hist["home_win"].notna()].copy()
    hist["date"] = pd.to_datetime(hist["date"])
    hist["home_team"] = hist["home_team"].map(normalize_fighter_name)
    hist["away_team"] = hist["away_team"].map(normalize_fighter_name)
    rest_fill = _train_rest_fill(hist)

    slate = slate_rows.copy()
    slate["home_team"] = slate["home_team"].map(normalize_fighter_name)
    slate["away_team"] = slate["away_team"].map(normalize_fighter_name)
    slate["date"] = pd.to_datetime(slate["date"])
    if "season" not in slate.columns:
        slate["season"] = slate["date"].dt.year
    slate["home_rest_days"] = rest_fill
    slate["away_rest_days"] = rest_fill
    slate["home_b2b"] = 0
    slate["away_b2b"] = 0

    slate_ids = set(slate["fight_id"].astype(str))
    slate_min_date = pd.to_datetime(slate["date"]).min()
    hist = hist[~hist["fight_id"].astype(str).isin(slate_ids)].copy()
    hist_before = hist[hist["date"] < slate_min_date].copy()
    tracker = build_fighter_tracker_from_history(hist_before)
    fight_stats = _load_fight_stats_df()
    stats_for_loop = None
    if fight_stats is not None:
        stats_for_loop = fight_stats[
            fight_stats["date"] < slate_min_date
        ].copy()
        stats_for_loop = stats_for_loop[
            ~stats_for_loop["fight_id"].astype(str).isin(slate_ids)
        ]
        slate_stats = fight_stats[fight_stats["fight_id"].astype(str).isin(slate_ids)]
        if not slate_stats.empty:
            stats_for_loop = pd.concat([stats_for_loop, slate_stats], ignore_index=True)
    combined = pd.concat([hist_before, slate], ignore_index=True, sort=False)
    combined["date"] = pd.to_datetime(combined["date"])
    combined = combined.sort_values(["date", "fight_id"]).reset_index(drop=True)
    full = build_features(
        combined,
        rest_fill=rest_fill,
        tracker=tracker,
        fight_stats_df=stats_for_loop,
        attach_elo=True,
    )
    return full[full["fight_id"].astype(str).isin(slate_ids)].drop_duplicates(
        subset=["fight_id"], keep="last"
    ).copy()
