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


@dataclass
class _FightRecord:
    date: pd.Timestamp
    fighter: str
    win: int


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


def _row_features(
    row,
    tracker: _FighterTracker,
    rest_fill: float,
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

    return {
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


def build_features(
    fights_df: pd.DataFrame,
    *,
    rest_fill: float = DEFAULT_REST_FILL,
    update_state: bool = True,
    tracker: _FighterTracker | None = None,
    attach_elo: bool = True,
) -> pd.DataFrame:
    df = fights_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["date", "fight_id"]).reset_index(drop=True)
    state = tracker if tracker is not None else _FighterTracker()
    rows: list[dict] = []

    for row in df.itertuples(index=False):
        feats = _row_features(row, state, rest_fill)
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
    return build_features(fights, rest_fill=rest_fill)


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
    combined = pd.concat([hist_before, slate], ignore_index=True, sort=False)
    combined["date"] = pd.to_datetime(combined["date"])
    combined = combined.sort_values(["date", "fight_id"]).reset_index(drop=True)
    full = build_features(
        combined,
        rest_fill=rest_fill,
        tracker=tracker,
        attach_elo=True,
    )
    return full[full["fight_id"].astype(str).isin(slate_ids)].drop_duplicates(
        subset=["fight_id"], keep="last"
    ).copy()
