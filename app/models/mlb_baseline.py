"""MLB home_win baseline model and evaluation helpers."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app.config import PROJECT_ROOT
from app.db.database import get_connection

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
    train: pd.DataFrame, test: pd.DataFrame
) -> tuple[Pipeline, HoldoutMetrics]:
    x_train = train[FEATURE_COLUMNS].values
    y_train = train["home_win"].values
    x_test = test[FEATURE_COLUMNS].values
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
    metrics = compute_metrics("logistic_regression", y_test, prob)
    return model, metrics


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

    y_test = test["home_win"].values

    full_prepared = prepare_features(
        pd.concat([train_raw, test_raw], ignore_index=True),
        era_medians,
        rest_fill,
    )
    elo_test_probs = predict_elo(full_prepared)[len(train_raw) :]
    home_probs = predict_home_always(test)

    model, model_metrics = train_logistic(train, test)
    home_metrics = compute_metrics("home_always_wins", y_test, home_probs)
    elo_metrics = compute_metrics("elo_baseline", y_test, elo_test_probs)

    MODEL_ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "model": model,
            "feature_columns": FEATURE_COLUMNS,
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
        "imputation": {
            "era": "season median from 2023-2024 training games",
            "last10_win_pct": NEUTRAL_LAST10_WIN_PCT,
            "last10_run_diff": NEUTRAL_LAST10_RUN_DIFF,
            "rest_days": rest_fill,
        },
        "metrics": {
            m.name: {
                "log_loss": m.log_loss,
                "brier": m.brier,
                "accuracy": m.accuracy,
            }
            for m in (home_metrics, elo_metrics, model_metrics)
        },
        "phase_gate": {
            "beats_home_baseline_log_loss": model_metrics.log_loss
            < home_metrics.log_loss,
            "beats_elo_baseline_log_loss": model_metrics.log_loss
            < elo_metrics.log_loss,
        },
    }
    METRICS_JSON.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results


def format_metrics_table(results: dict) -> str:
    lines = [
        "| Model | Log loss | Brier | Accuracy |",
        "|-------|----------|-------|----------|",
    ]
    for name, m in results["metrics"].items():
        lines.append(
            f"| {name} | {m['log_loss']:.4f} | {m['brier']:.4f} | {m['accuracy']:.3f} |"
        )
    return "\n".join(lines)
