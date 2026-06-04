"""MLB home_win baseline model and evaluation helpers."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app.config import PROJECT_ROOT
from app.db.database import get_connection
from app.models.calibration import (
    favorite_pick_agreement_rate,
    market_log_loss_holdout,
)

MODEL_ARTIFACT = PROJECT_ROOT / "data" / "processed" / "mlb_baseline_model.joblib"
METRICS_JSON = PROJECT_ROOT / "data" / "processed" / "mlb_baseline_metrics.json"
PARQUET_PATH = PROJECT_ROOT / "data" / "processed" / "mlb_games.parquet"

FEATURE_COLUMNS = [
    "home_pitcher_era",
    "away_pitcher_era",
    "home_last10_win_pct",
    "away_last10_win_pct",
    "home_last10_run_diff",
    "away_last10_run_diff",
    "home_rest_days",
    "away_rest_days",
]

FEATURE_COLUMNS_V2 = FEATURE_COLUMNS + ["elo_home_pre", "elo_away_pre"]

NEUTRAL_LAST10_WIN_PCT = 0.5
NEUTRAL_LAST10_RUN_DIFF = 0.0
DEFAULT_REST_DAYS = 1

ELO_START = 1500.0
ELO_K = 20.0
ELO_HOME_ADV = 24.0


@dataclass
class HoldoutMetrics:
    name: str
    log_loss: float
    brier: float
    accuracy: float


def load_games() -> pd.DataFrame:
    if PARQUET_PATH.exists():
        df = pd.read_parquet(PARQUET_PATH)
    else:
        conn = get_connection()
        try:
            df = pd.read_sql("SELECT * FROM mlb_games ORDER BY date, game_id", conn)
        finally:
            conn.close()
    df["date"] = pd.to_datetime(df["date"])
    df["season"] = df["date"].dt.year
    return df.sort_values(["date", "game_id"]).reset_index(drop=True)


def _season_era_medians(train: pd.DataFrame) -> dict[int, float]:
    home = train[["season", "home_pitcher_era"]].rename(columns={"home_pitcher_era": "era"})
    away = train[["season", "away_pitcher_era"]].rename(columns={"away_pitcher_era": "era"})
    combined = pd.concat([home, away], ignore_index=True)
    medians = combined.groupby("season")["era"].median()
    overall = float(combined["era"].median())
    return {int(s): float(m) for s, m in medians.items()} | {"default": overall}


def prepare_features(
    df: pd.DataFrame, era_medians: dict[int, float], rest_fill: float
) -> pd.DataFrame:
    out = df.copy()
    for side in ("home", "away"):
        col = f"{side}_pitcher_era"
        out[col] = out.apply(
            lambda r, c=col: (
                r[c]
                if pd.notna(r[c])
                else era_medians.get(int(r["season"]), era_medians["default"])
            ),
            axis=1,
        )
    for col in ("home_last10_win_pct", "away_last10_win_pct"):
        out[col] = out[col].fillna(NEUTRAL_LAST10_WIN_PCT)
    for col in ("home_last10_run_diff", "away_last10_run_diff"):
        out[col] = out[col].fillna(NEUTRAL_LAST10_RUN_DIFF)
    for col in ("home_rest_days", "away_rest_days"):
        out[col] = out[col].fillna(rest_fill)
    return out


def time_split(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    train = df[df["season"].isin([2023, 2024])].copy()
    test = df[df["season"] == 2025].copy()
    if train.empty or test.empty:
        raise ValueError("Expected 2023-2024 train rows and 2025 holdout rows")
    return train, test


def _elo_expected(home_elo: float, away_elo: float) -> float:
    return 1.0 / (1.0 + 10 ** ((away_elo - home_elo - ELO_HOME_ADV) / 400.0))


def _elo_update(
    home_elo: float, away_elo: float, home_win: int
) -> tuple[float, float]:
    expected = _elo_expected(home_elo, away_elo)
    actual = float(home_win)
    home_elo += ELO_K * (actual - expected)
    away_elo += ELO_K * ((1.0 - actual) - (1.0 - expected))
    return home_elo, away_elo


def attach_elo_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add pre-game Elo ratings (chronological, no leakage)."""
    out = df.copy()
    ratings: dict[str, float] = {}
    home_elos: list[float] = []
    away_elos: list[float] = []
    for row in out.itertuples(index=False):
        home_elos.append(ratings.get(row.home_team, ELO_START))
        away_elos.append(ratings.get(row.away_team, ELO_START))
        home, away = _elo_update(
            ratings.get(row.home_team, ELO_START),
            ratings.get(row.away_team, ELO_START),
            int(row.home_win),
        )
        ratings[row.home_team] = home
        ratings[row.away_team] = away
    out["elo_home_pre"] = home_elos
    out["elo_away_pre"] = away_elos
    return out


def predict_elo(df: pd.DataFrame) -> np.ndarray:
    ratings: dict[str, float] = {}
    probs: list[float] = []
    for row in df.itertuples(index=False):
        home = ratings.get(row.home_team, ELO_START)
        away = ratings.get(row.away_team, ELO_START)
        prob = _elo_expected(home, away)
        probs.append(prob)
        home, away = _elo_update(home, away, int(row.home_win))
        ratings[row.home_team] = home
        ratings[row.away_team] = away
    return np.array(probs)


def predict_home_always(_df: pd.DataFrame) -> np.ndarray:
    return np.ones(len(_df))


def compute_metrics(name: str, y_true: np.ndarray, y_prob: np.ndarray) -> HoldoutMetrics:
    y_prob = np.clip(y_prob, 1e-6, 1 - 1e-6)
    return HoldoutMetrics(
        name=name,
        log_loss=float(log_loss(y_true, y_prob)),
        brier=float(brier_score_loss(y_true, y_prob)),
        accuracy=float(accuracy_score(y_true, y_prob >= 0.5)),
    )


def train_logistic(
    train: pd.DataFrame,
    test: pd.DataFrame,
    feature_cols: list[str] | None = None,
) -> tuple[Pipeline, HoldoutMetrics]:
    cols = feature_cols or FEATURE_COLUMNS
    x_train = train[cols].values
    y_train = train["home_win"].values
    x_test = test[cols].values
    y_test = test["home_win"].values

    model = Pipeline(
        [
            ("scaler", StandardScaler()),
            (
                "clf",
                LogisticRegression(max_iter=1000, random_state=42),
            ),
        ]
    )
    model.fit(x_train, y_train)
    prob = model.predict_proba(x_test)[:, 1]
    metrics = compute_metrics("logistic_regression_v1", y_test, prob)
    return model, metrics


def train_gbc_v2(
    train: pd.DataFrame, test: pd.DataFrame
) -> tuple[GradientBoostingClassifier, HoldoutMetrics]:
    clf = GradientBoostingClassifier(
        n_estimators=120,
        max_depth=3,
        learning_rate=0.08,
        random_state=42,
    )
    x_train = train[FEATURE_COLUMNS_V2].values
    y_train = train["home_win"].values
    x_test = test[FEATURE_COLUMNS_V2].values
    y_test = test["home_win"].values
    clf.fit(x_train, y_train)
    prob = clf.predict_proba(x_test)[:, 1]
    metrics = compute_metrics("gradient_boosting_v2_elo", y_test, prob)
    return clf, metrics


def _favorite_agreement_v1_market(
    test_raw: pd.DataFrame,
    test_prepared: pd.DataFrame,
    v1_model: Pipeline,
) -> dict[str, Any]:
    from app.odds.mlb_odds_free import ODDS_2025_CSV
    from app.odds.odds_math import market_probs_from_american
    from app.odds.team_aliases import is_valid_american_odds, normalize_team_name

    if not ODDS_2025_CSV.exists():
        return {"n_market_home_favorite": 0, "agreement_rate": None}

    odds = pd.read_csv(ODDS_2025_CSV)
    odds["date"] = pd.to_datetime(odds["date"]).dt.strftime("%Y-%m-%d")
    holdout = test_raw.copy()
    holdout["date"] = holdout["date"].dt.strftime("%Y-%m-%d")
    holdout["home_team"] = holdout["home_team"].map(normalize_team_name)
    holdout["away_team"] = holdout["away_team"].map(normalize_team_name)
    tp = test_prepared.copy()
    tp["date"] = holdout["date"].values if "date" not in tp.columns else pd.to_datetime(tp["date"]).dt.strftime("%Y-%m-%d")
    tp["date"] = test_raw["date"].dt.strftime("%Y-%m-%d").values
    tp["home_team"] = test_raw["home_team"].map(normalize_team_name).values
    tp["away_team"] = test_raw["away_team"].map(normalize_team_name).values
    merged = tp.merge(odds, on=["date", "home_team", "away_team"], how="inner")
    valid = merged[
        merged.apply(
            lambda r: is_valid_american_odds(r.home_ml) and is_valid_american_odds(r.away_ml),
            axis=1,
        )
    ]
    if valid.empty:
        return {"n_market_home_favorite": 0, "agreement_rate": None}
    model_probs = v1_model.predict_proba(valid[FEATURE_COLUMNS].values)[:, 1]
    market_probs = []
    for row in valid.itertuples(index=False):
        mh, _ = market_probs_from_american(int(row.home_ml), int(row.away_ml))
        market_probs.append(mh)
    return favorite_pick_agreement_rate(model_probs, np.array(market_probs))


def current_elo_ratings(history: pd.DataFrame) -> dict[str, float]:
    ratings: dict[str, float] = {}
    for row in history.sort_values(["date", "game_id"]).itertuples(index=False):
        home, away = _elo_update(
            ratings.get(row.home_team, ELO_START),
            ratings.get(row.away_team, ELO_START),
            int(row.home_win),
        )
        ratings[row.home_team] = home
        ratings[row.away_team] = away
    return ratings


def attach_elo_for_slate(df: pd.DataFrame, history: pd.DataFrame | None = None) -> pd.DataFrame:
    """Elo ratings before each row's game date (for live inference)."""
    hist = history if history is not None else load_games()
    out = df.copy()
    min_date = pd.to_datetime(out["date"]).min()
    hist = hist[hist["date"] < min_date]
    ratings = current_elo_ratings(hist) if not hist.empty else {}
    home_elos = [ratings.get(t, ELO_START) for t in out["home_team"]]
    away_elos = [ratings.get(t, ELO_START) for t in out["away_team"]]
    out["elo_home_pre"] = home_elos
    out["elo_away_pre"] = away_elos
    return out


def run_training() -> dict:
    raw = load_games()
    train_raw, test_raw = time_split(raw)

    era_medians = _season_era_medians(train_raw)
    rest_fill = float(
        pd.concat([train_raw["home_rest_days"], train_raw["away_rest_days"]])
        .dropna()
        .median()
    )
    if math.isnan(rest_fill):
        rest_fill = float(DEFAULT_REST_DAYS)

    train = prepare_features(train_raw, era_medians, rest_fill)
    test = prepare_features(test_raw, era_medians, rest_fill)
    full = prepare_features(
        pd.concat([train_raw, test_raw], ignore_index=True),
        era_medians,
        rest_fill,
    )
    full = attach_elo_features(full)
    train = full.iloc[: len(train)].copy()
    test = full.iloc[len(train) :].copy()

    y_test = test["home_win"].values
    n_train = len(train_raw)

    elo_test_probs = predict_elo(full)[n_train:]
    home_probs = predict_home_always(test)

    v1_model, v1_metrics = train_logistic(train, test, FEATURE_COLUMNS)
    v2_model, v2_metrics = train_gbc_v2(train, test)
    home_metrics = compute_metrics("home_always_wins", y_test, home_probs)
    elo_metrics = compute_metrics("elo_baseline", y_test, elo_test_probs)

    market_ll = market_log_loss_holdout(raw)
    market_metrics = (
        {"log_loss": market_ll, "brier": None, "accuracy": None}
        if market_ll is not None
        else None
    )

    fav_agreement = _favorite_agreement_v1_market(test_raw, test, v1_model)

    replace_with_v2 = market_ll is not None and v2_metrics.log_loss < market_ll
    production_model = v2_model if replace_with_v2 else v1_model
    production_cols = FEATURE_COLUMNS_V2 if replace_with_v2 else FEATURE_COLUMNS
    production_version = "v2_gbc_elo" if replace_with_v2 else "v1_logistic"
    production_metrics = v2_metrics if replace_with_v2 else v1_metrics

    MODEL_ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "model": production_model,
            "model_version": production_version,
            "feature_columns": production_cols,
            "era_medians": era_medians,
            "rest_fill": rest_fill,
            "neutral_last10_win_pct": NEUTRAL_LAST10_WIN_PCT,
            "neutral_last10_run_diff": NEUTRAL_LAST10_RUN_DIFF,
        },
        MODEL_ARTIFACT,
    )

    results = {
        "train_seasons": [2023, 2024],
        "holdout_season": 2025,
        "train_rows": len(train),
        "holdout_rows": len(test),
        "production_model": production_version,
        "replaced_artifact": replace_with_v2,
        "imputation": {
            "era": "season median from 2023-2024 training games",
            "last10_win_pct": NEUTRAL_LAST10_WIN_PCT,
            "last10_run_diff": NEUTRAL_LAST10_RUN_DIFF,
            "rest_days": rest_fill,
        },
        "calibration": {
            "favorite_pick_agreement": fav_agreement,
            "display_blend": "0.5 * model + 0.5 * market (UI only)",
        },
        "metrics": {
            m.name: {
                "log_loss": m.log_loss,
                "brier": m.brier,
                "accuracy": m.accuracy,
            }
            for m in (home_metrics, elo_metrics, v1_metrics, v2_metrics)
        },
        "market_implied_baseline": market_metrics,
        "phase_gate": {
            "v1_beats_elo": v1_metrics.log_loss < elo_metrics.log_loss,
            "v2_beats_market_log_loss": replace_with_v2,
        },
    }
    if market_metrics:
        results["metrics"]["market_implied"] = market_metrics
    METRICS_JSON.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results


def load_model_artifact() -> dict:
    if not MODEL_ARTIFACT.exists():
        raise FileNotFoundError(
            f"Model not found at {MODEL_ARTIFACT}. Run scripts/train_mlb_baseline.py first."
        )
    return joblib.load(MODEL_ARTIFACT)


def predict_home_win_proba(df: pd.DataFrame) -> np.ndarray:
    artifact = load_model_artifact()
    prepared = prepare_features(
        df, artifact["era_medians"], artifact["rest_fill"]
    )
    cols = artifact.get("feature_columns", FEATURE_COLUMNS)
    if "elo_home_pre" in cols:
        prepared = attach_elo_for_slate(prepared)
    return artifact["model"].predict_proba(prepared[cols].values)[:, 1]


def format_metrics_table(results: dict) -> str:
    lines = [
        "| Model | Log loss | Brier | Accuracy |",
        "|-------|----------|-------|----------|",
    ]
    for name, m in results["metrics"].items():
        brier = m.get("brier")
        acc = m.get("accuracy")
        brier_s = f"{brier:.4f}" if brier is not None else "—"
        acc_s = f"{acc:.3f}" if acc is not None else "—"
        lines.append(
            f"| {name} | {m['log_loss']:.4f} | {brier_s} | {acc_s} |"
        )
    return "\n".join(lines)
