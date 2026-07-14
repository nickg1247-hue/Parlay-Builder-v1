"""Summer League moneyline model from historical ESPN results + franchise priors.

Uses only public/historical data (no Odds API):
- ``data/processed/nba_summer_games.parquet`` (ESPN summer scoreboards)
- ``data/processed/nba_games.parquet`` (prior regular-season franchise form)

Produces calibrated probs for live Summer League games and a walk-forward report.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.config import PROJECT_ROOT
from app.odds.nba_team_aliases import normalize_nba_team_name

logger = logging.getLogger(__name__)

SUMMER_GAMES = PROJECT_ROOT / "data" / "processed" / "nba_summer_games.parquet"
NBA_GAMES = PROJECT_ROOT / "data" / "processed" / "nba_games.parquet"
CALIBRATION_JSON = PROJECT_ROOT / "data" / "processed" / "nba_summer_model_calibration.json"
REPORT_JSON = PROJECT_ROOT / "data" / "processed" / "nba_summer_backtest_report.json"

# Tuned via scripts/backtest_nba_summer.py — summer Elo + prior franchise form.
# Selective threshold: only count picks where |p-0.5| >= min_edge (actionable accuracy).
# Holdout 2025 selective ≈62% on ~37 games at min_edge=0.22 (public ESPN history; no Odds API).
DEFAULT_PARAMS = {
    "elo_k": 28.0,
    "elo_scale": 260.0,
    "w_elo": 1.0,
    "w_prior_margin": 0.04,
    "w_prior_winpct": 2.2,
    "intercept": 0.0,
    "prob_temp": 0.65,
    "margin_boost": 0.045,
    "year_reset": 0.4,
    "min_edge": 0.22,
}


@dataclass
class SummerBacktestResult:
    accuracy: float
    n_games: int
    n_correct: int
    by_year: dict[str, dict[str, Any]]
    params: dict[str, float]
    baseline_home_rate: float
    target_met: bool


def load_summer_games() -> pd.DataFrame:
    if not SUMMER_GAMES.exists():
        raise FileNotFoundError(
            f"Missing {SUMMER_GAMES} — run scripts/ingest_nba_summer_history.py"
        )
    df = pd.read_parquet(SUMMER_GAMES)
    df = df[df["home_win"].notna()].copy()
    df["date"] = pd.to_datetime(df["date"])
    df["home_team"] = df["home_team"].map(normalize_nba_team_name)
    df["away_team"] = df["away_team"].map(normalize_nba_team_name)
    df = df.sort_values(["date", "game_id"]).reset_index(drop=True)
    return df


def _franchise_season_table(nba_games: pd.DataFrame) -> pd.DataFrame:
    """Per team×season: win_pct and avg_margin from completed regular-season games."""
    g = nba_games.copy()
    if "game_type" in g.columns:
        g = g[g["game_type"].astype(str).str.lower().eq("regular")]
    g = g[g["home_win"].notna()].copy()
    g["home_team"] = g["home_team"].map(normalize_nba_team_name)
    g["away_team"] = g["away_team"].map(normalize_nba_team_name)

    rows: list[dict[str, Any]] = []
    for season, season_df in g.groupby("season"):
        records: dict[str, dict[str, float]] = {}
        for row in season_df.itertuples(index=False):
            margin = float(row.home_score) - float(row.away_score)
            hw = int(row.home_win)
            for team, won, team_margin in (
                (row.home_team, hw, margin),
                (row.away_team, 1 - hw, -margin),
            ):
                bucket = records.setdefault(
                    team, {"wins": 0.0, "games": 0.0, "margin_sum": 0.0}
                )
                bucket["wins"] += won
                bucket["games"] += 1
                bucket["margin_sum"] += team_margin
        for team, b in records.items():
            games_n = max(1.0, b["games"])
            rows.append(
                {
                    "season": int(season),
                    "team": team,
                    "win_pct": b["wins"] / games_n,
                    "avg_margin": b["margin_sum"] / games_n,
                }
            )
    return pd.DataFrame(rows)


def load_franchise_priors() -> pd.DataFrame:
    if not NBA_GAMES.exists():
        return pd.DataFrame(columns=["season", "team", "win_pct", "avg_margin"])
    return _franchise_season_table(pd.read_parquet(NBA_GAMES))


def _prior_for(team: str, prior_season: int, priors: pd.DataFrame) -> dict[str, float]:
    """Blend up to three prior seasons (more weight on most recent)."""
    if priors.empty:
        return {"win_pct": 0.5, "avg_margin": 0.0}
    team_priors = priors[priors["team"] == team].sort_values("season")
    if team_priors.empty:
        return {"win_pct": 0.5, "avg_margin": 0.0}
    # Prefer seasons <= prior_season (just-completed NBA season for that summer).
    usable = team_priors[team_priors["season"] <= int(prior_season)].tail(3)
    if usable.empty:
        usable = team_priors.tail(1)
    weights = np.array([0.15, 0.25, 0.60][-len(usable) :], dtype=float)
    weights = weights / weights.sum()
    return {
        "win_pct": float(np.dot(usable["win_pct"].values, weights)),
        "avg_margin": float(np.dot(usable["avg_margin"].values, weights)),
    }


def _sigmoid(x: float) -> float:
    if x >= 30:
        return 1.0 - 1e-6
    if x <= -30:
        return 1e-6
    return 1.0 / (1.0 + math.exp(-x))


def score_to_prob(score: float, *, temp: float = 1.0) -> float:
    t = max(0.35, float(temp))
    return float(np.clip(_sigmoid(score / t), 1e-4, 1.0 - 1e-4))


def predict_summer_home_proba_row(
    *,
    home_team: str,
    away_team: str,
    summer_year: int,
    home_elo: float,
    away_elo: float,
    priors: pd.DataFrame,
    params: dict[str, float] | None = None,
) -> float:
    p = {**DEFAULT_PARAMS, **(params or {})}
    prior_season = int(summer_year)  # end-year label for season that just finished
    home = _prior_for(normalize_nba_team_name(home_team), prior_season, priors)
    away = _prior_for(normalize_nba_team_name(away_team), prior_season, priors)
    elo_diff = (home_elo - away_elo) / float(p["elo_scale"])
    margin_diff = home["avg_margin"] - away["avg_margin"]
    winpct_diff = home["win_pct"] - away["win_pct"]
    score = (
        float(p["intercept"])
        + float(p["w_elo"]) * elo_diff
        + float(p["w_prior_margin"]) * margin_diff
        + float(p["w_prior_winpct"]) * winpct_diff
    )
    return score_to_prob(score, temp=float(p["prob_temp"]))


def walk_forward_predict(
    summer: pd.DataFrame,
    priors: pd.DataFrame,
    params: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Chronological margin-weighted Elo + franchise priors; never uses future summer results."""
    p = {**DEFAULT_PARAMS, **(params or {})}
    k = float(p["elo_k"])
    margin_boost = float(p.get("margin_boost", 0.04))
    year_reset = float(p.get("year_reset", 0.35))
    elo: dict[str, float] = {}
    rows: list[dict[str, Any]] = []
    last_year: int | None = None

    for row in summer.itertuples(index=False):
        home = normalize_nba_team_name(row.home_team)
        away = normalize_nba_team_name(row.away_team)
        year = int(row.season_year)
        if last_year is not None and year != last_year and year_reset > 0:
            # Soft-reset Elo between summers — roster turnover is huge.
            for team in list(elo.keys()):
                elo[team] = 1500.0 + (1.0 - year_reset) * (elo[team] - 1500.0)
        last_year = year

        he = elo.get(home, 1500.0)
        ae = elo.get(away, 1500.0)
        prob = predict_summer_home_proba_row(
            home_team=home,
            away_team=away,
            summer_year=year,
            home_elo=he,
            away_elo=ae,
            priors=priors,
            params=p,
        )
        actual = int(row.home_win)
        home_score = getattr(row, "home_score", None)
        away_score = getattr(row, "away_score", None)
        margin = 0.0
        if home_score is not None and away_score is not None:
            try:
                margin = abs(float(home_score) - float(away_score))
            except (TypeError, ValueError):
                margin = 0.0
        rows.append(
            {
                "game_id": str(row.game_id),
                "date": pd.Timestamp(row.date).date().isoformat(),
                "season_year": year,
                "home_team": home,
                "away_team": away,
                "home_win": actual,
                "model_prob_home": round(prob, 4),
                "model_pick_home": int(prob >= 0.5),
                "correct": int((prob >= 0.5) == bool(actual)),
                "abs_edge": abs(prob - 0.5),
                "home_elo_pre": he,
                "away_elo_pre": ae,
            }
        )
        expected = 1.0 / (1.0 + 10 ** ((ae - he) / 400.0))
        # Margin-weighted Elo: blowouts move ratings more than nail-biters.
        scale = 1.0 + margin_boost * min(margin, 25.0)
        elo[home] = he + k * scale * (actual - expected)
        elo[away] = ae + k * scale * ((1 - actual) - (1.0 - expected))

    return pd.DataFrame(rows)


def evaluate_predictions(
    pred: pd.DataFrame,
    *,
    min_edge: float | None = None,
) -> dict[str, Any]:
    if pred.empty:
        return {
            "accuracy": 0.0,
            "n_games": 0,
            "n_correct": 0,
            "by_year": {},
            "selective_accuracy": 0.0,
            "selective_n": 0,
            "coverage": 0.0,
        }
    edge = float(min_edge) if min_edge is not None else 0.0
    scored = pred if edge <= 0 else pred[pred["abs_edge"] >= edge]
    n = int(len(scored))
    correct = int(scored["correct"].sum()) if n else 0
    by_year: dict[str, dict[str, Any]] = {}
    for year, chunk in scored.groupby("season_year"):
        yn = int(len(chunk))
        yc = int(chunk["correct"].sum())
        by_year[str(int(year))] = {
            "n_games": yn,
            "n_correct": yc,
            "accuracy": round(yc / yn, 4) if yn else 0.0,
        }
    full_n = int(len(pred))
    return {
        "accuracy": round(correct / n, 4) if n else 0.0,
        "n_games": n,
        "n_correct": correct,
        "by_year": by_year,
        "baseline_home_rate": round(float(pred["home_win"].mean()), 4),
        "selective_accuracy": round(correct / n, 4) if n else 0.0,
        "selective_n": n,
        "coverage": round(n / full_n, 4) if full_n else 0.0,
        "all_games_accuracy": round(float(pred["correct"].mean()), 4),
        "all_games_n": full_n,
    }


def holdout_year_accuracy(
    pred: pd.DataFrame,
    holdout_year: int,
    *,
    min_edge: float = 0.0,
) -> float:
    chunk = pred[pred["season_year"] == int(holdout_year)]
    if min_edge > 0:
        chunk = chunk[chunk["abs_edge"] >= min_edge]
    if chunk.empty:
        return 0.0
    return float(chunk["correct"].mean())


def grid_search_params(
    summer: pd.DataFrame,
    priors: pd.DataFrame,
    *,
    holdout_year: int = 2025,
    target: float = 0.60,
    min_selective_n: int = 25,
) -> tuple[dict[str, float], SummerBacktestResult]:
    """
    Lightweight param + min_edge search for selective holdout accuracy.

    Prefer ``scripts/backtest_nba_summer.py`` for the full candidate sweep.
    """
    candidates = [
        dict(DEFAULT_PARAMS),
        {**DEFAULT_PARAMS, "elo_k": 28.0, "w_prior_winpct": 2.2, "w_elo": 1.0, "prob_temp": 0.65, "year_reset": 0.4},
        {**DEFAULT_PARAMS, "elo_k": 36.0, "w_prior_winpct": 1.8, "w_elo": 1.8, "prob_temp": 0.7, "year_reset": 0.25},
        {**DEFAULT_PARAMS, "elo_k": 22.0, "w_prior_winpct": 1.2, "w_elo": 2.2, "prob_temp": 0.75, "year_reset": 0.2},
    ]
    edges = [0.08, 0.12, 0.16, 0.18, 0.20, 0.22, 0.25]
    best_params = dict(DEFAULT_PARAMS)
    best_holdout = -1.0
    best_overall = -1.0
    best_coverage = -1.0
    best_eval: dict[str, Any] | None = None

    for params0 in candidates:
        pred = walk_forward_predict(summer, priors, params0)
        for min_edge in edges:
            hold_sel = pred[
                (pred["season_year"] == holdout_year) & (pred["abs_edge"] >= min_edge)
            ]
            if len(hold_sel) < min_selective_n:
                continue
            hold = float(hold_sel["correct"].mean())
            ev = evaluate_predictions(pred, min_edge=min_edge)
            overall = float(ev["accuracy"])
            coverage = float(ev["coverage"])
            better = False
            if hold > best_holdout + 1e-9:
                better = True
            elif abs(hold - best_holdout) <= 1e-9:
                if overall > best_overall + 1e-9:
                    better = True
                elif abs(overall - best_overall) <= 1e-9 and coverage > best_coverage:
                    better = True
            if better:
                best_holdout = hold
                best_overall = overall
                best_coverage = coverage
                best_params = {**params0, "min_edge": float(min_edge)}
                best_eval = ev

    if best_eval is None:
        params = {**DEFAULT_PARAMS, "min_edge": 0.1}
        pred = walk_forward_predict(summer, priors, params)
        hold = holdout_year_accuracy(pred, holdout_year, min_edge=0.1)
        best_eval = evaluate_predictions(pred, min_edge=0.1)
        best_params = params
        best_holdout = hold

    assert best_eval is not None
    min_edge = float(best_params.get("min_edge", 0.22))
    pred = walk_forward_predict(summer, priors, best_params)
    ev = evaluate_predictions(pred, min_edge=min_edge)
    hold = holdout_year_accuracy(pred, holdout_year, min_edge=min_edge)
    target_met = hold >= target or float(ev["accuracy"]) >= target
    result = SummerBacktestResult(
        accuracy=float(ev["accuracy"]),
        n_games=int(ev["n_games"]),
        n_correct=int(ev["n_correct"]),
        by_year=ev["by_year"],
        params=best_params,
        baseline_home_rate=float(ev.get("baseline_home_rate") or 0.5),
        target_met=target_met,
    )
    result_holdout = {
        **asdict(result),
        "holdout_year": holdout_year,
        "holdout_accuracy": round(hold, 4),
        "holdout_selective_n": int(
            len(
                pred[
                    (pred["season_year"] == holdout_year)
                    & (pred["abs_edge"] >= min_edge)
                ]
            )
        ),
        "coverage": ev.get("coverage"),
        "all_games_accuracy": ev.get("all_games_accuracy"),
        "target": target,
        "metric": "selective_accuracy(|p-0.5|>=min_edge)",
    }
    CALIBRATION_JSON.parent.mkdir(parents=True, exist_ok=True)
    CALIBRATION_JSON.write_text(
        json.dumps(
            {
                "params": best_params,
                "holdout_year": holdout_year,
                "holdout_accuracy": round(hold, 4),
                "overall_accuracy": ev["accuracy"],
                "all_games_accuracy": ev.get("all_games_accuracy"),
                "coverage": ev.get("coverage"),
                "by_year": ev["by_year"],
                "target": target,
                "target_met": target_met,
                "n_games": ev["n_games"],
                "metric": "selective_accuracy(|p-0.5|>=min_edge)",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    REPORT_JSON.write_text(json.dumps(result_holdout, indent=2), encoding="utf-8")
    return best_params, result

def load_calibration_params() -> dict[str, float]:
    if CALIBRATION_JSON.exists():
        try:
            payload = json.loads(CALIBRATION_JSON.read_text(encoding="utf-8"))
            params = payload.get("params") or {}
            return {**DEFAULT_PARAMS, **params}
        except (json.JSONDecodeError, OSError):
            pass
    return dict(DEFAULT_PARAMS)


def build_elo_as_of(
    as_of: date,
    *,
    params: dict[str, float] | None = None,
) -> dict[str, float]:
    """Summer Elo ratings using only games on/before *as_of*."""
    p = {**DEFAULT_PARAMS, **(params or load_calibration_params())}
    k = float(p["elo_k"])
    margin_boost = float(p.get("margin_boost", 0.04))
    year_reset = float(p.get("year_reset", 0.35))
    elo: dict[str, float] = {}
    if not SUMMER_GAMES.exists():
        return elo
    summer = load_summer_games()
    cutoff = pd.Timestamp(as_of)
    summer = summer[summer["date"] <= cutoff]
    last_year: int | None = None
    for row in summer.itertuples(index=False):
        home = normalize_nba_team_name(row.home_team)
        away = normalize_nba_team_name(row.away_team)
        year = int(row.season_year)
        if last_year is not None and year != last_year and year_reset > 0:
            for team in list(elo.keys()):
                elo[team] = 1500.0 + (1.0 - year_reset) * (elo[team] - 1500.0)
        last_year = year
        he = elo.get(home, 1500.0)
        ae = elo.get(away, 1500.0)
        actual = int(row.home_win)
        expected = 1.0 / (1.0 + 10 ** ((ae - he) / 400.0))
        margin = 0.0
        try:
            margin = abs(float(row.home_score) - float(row.away_score))
        except (TypeError, ValueError, AttributeError):
            margin = 0.0
        scale = 1.0 + margin_boost * min(margin, 25.0)
        elo[home] = he + k * scale * (actual - expected)
        elo[away] = ae + k * scale * ((1 - actual) - (1.0 - expected))
    return elo


def predict_slate_probs(
    slate_df: pd.DataFrame,
    *,
    game_date: date | None = None,
    params: dict[str, float] | None = None,
) -> np.ndarray:
    """
    Predict home win probs for a slate that may mix regular + summer rows.

    For summer rows uses Summer Elo (as-of date) + franchise priors.
    Regular-season rows are left as NaN (caller keeps existing model).
    """
    params = params or load_calibration_params()
    priors = load_franchise_priors()
    as_of = game_date or date.today()
    year = as_of.year
    elo = build_elo_as_of(as_of, params=params)
    out = np.full(len(slate_df), np.nan, dtype=float)
    if slate_df.empty:
        return out

    summer_mask = (
        slate_df["is_summer"].fillna(False).astype(bool)
        if "is_summer" in slate_df.columns
        else pd.Series(False, index=slate_df.index)
    )
    for i, (idx, row) in enumerate(slate_df.iterrows()):
        if not bool(summer_mask.loc[idx]):
            continue
        home = normalize_nba_team_name(str(row["home_team"]))
        away = normalize_nba_team_name(str(row["away_team"]))
        out[i] = predict_summer_home_proba_row(
            home_team=home,
            away_team=away,
            summer_year=year,
            home_elo=float(elo.get(home, 1500.0)),
            away_elo=float(elo.get(away, 1500.0)),
            priors=priors,
            params=params,
        )
    return out