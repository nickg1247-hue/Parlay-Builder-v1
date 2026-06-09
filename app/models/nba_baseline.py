"""NBA home_win baseline model and evaluation helpers."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app.config import PROJECT_ROOT
from app.db.database import get_connection
from app.features.nba_pregame import (
    FEATURE_COLUMNS,
    FEATURE_COLUMNS_V1,
    FEATURE_COLUMNS_WAVE2,
    build_features_for_history,
)

MODEL_ARTIFACT = PROJECT_ROOT / "data" / "processed" / "nba_baseline_model.joblib"
METRICS_JSON = PROJECT_ROOT / "data" / "processed" / "nba_baseline_metrics.json"
MODELS_DIR = PROJECT_ROOT / "data" / "processed" / "models"
ACTIVE_NBA_MANIFEST = PROJECT_ROOT / "data" / "processed" / "active_nba_model.json"
PARQUET_PATH = PROJECT_ROOT / "data" / "processed" / "nba_games.parquet"

TRAIN_SEASONS = (2024, 2025)
HOLDOUT_SEASON = 2026

FEATURE_SET_V1 = "nba_v1"
FEATURE_SET_V2 = "v2_score_rolling"

NEUTRAL_LAST10_WIN_PCT = 0.5
NEUTRAL_SEASON_WIN_PCT = 0.5
DEFAULT_REST_FILL = 2.0
DEFAULT_PTS_FILL = 110.0

ELO_START = 1500.0
ELO_K = 20.0
ELO_HOME_ADV = 90.0


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
            df = pd.read_sql("SELECT * FROM nba_games ORDER BY date, game_id", conn)
        finally:
            conn.close()
    if df.empty:
        raise FileNotFoundError(
            f"No NBA games at {PARQUET_PATH}. Run scripts/ingest_nba.py first."
        )
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values(["date", "game_id"]).reset_index(drop=True)


def time_split(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    train = df[df["season"].isin(TRAIN_SEASONS)].copy()
    test = df[df["season"] == HOLDOUT_SEASON].copy()
    if train.empty or test.empty:
        raise ValueError(
            f"Expected train seasons {TRAIN_SEASONS} and holdout {HOLDOUT_SEASON}"
        )
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


def attach_elo_features(df: pd.DataFrame, *, update_ratings: bool = True) -> pd.DataFrame:
    out = df.copy()
    ratings: dict[str, float] = {}
    home_elos: list[float] = []
    away_elos: list[float] = []
    for row in out.itertuples(index=False):
        home_elos.append(ratings.get(row.home_team, ELO_START))
        away_elos.append(ratings.get(row.away_team, ELO_START))
        hw = getattr(row, "home_win", None)
        if update_ratings and hw is not None and pd.notna(hw):
            home, away = _elo_update(
                ratings.get(row.home_team, ELO_START),
                ratings.get(row.away_team, ELO_START),
                int(hw),
            )
            ratings[row.home_team] = home
            ratings[row.away_team] = away
    out["elo_home_pre"] = home_elos
    out["elo_away_pre"] = away_elos
    return out


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


def attach_elo_for_slate(
    df: pd.DataFrame, history: pd.DataFrame | None = None
) -> pd.DataFrame:
    hist = history if history is not None else load_games()
    out = df.copy()
    min_date = pd.to_datetime(out["date"]).min()
    prior = hist[hist["date"] < min_date]
    ratings = current_elo_ratings(prior) if not prior.empty else {}
    out["elo_home_pre"] = [ratings.get(t, ELO_START) for t in out["home_team"]]
    out["elo_away_pre"] = [ratings.get(t, ELO_START) for t in out["away_team"]]
    return out


def predict_elo(df: pd.DataFrame) -> np.ndarray:
    ratings: dict[str, float] = {}
    probs: list[float] = []
    for row in df.itertuples(index=False):
        home = ratings.get(row.home_team, ELO_START)
        away = ratings.get(row.away_team, ELO_START)
        probs.append(_elo_expected(home, away))
        home, away = _elo_update(home, away, int(row.home_win))
        ratings[row.home_team] = home
        ratings[row.away_team] = away
    return np.array(probs)


def predict_home_rate_constant(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    rate = float(train["home_win"].mean())
    return np.full(len(test), rate)


def predict_rolling_naive(test: pd.DataFrame) -> np.ndarray:
    """Home last-10 win % as probability (clipped)."""
    probs = test["home_last10_win_pct"].astype(float).values
    return np.clip(probs, 1e-6, 1 - 1e-6)


def compute_metrics(name: str, y_true: np.ndarray, y_prob: np.ndarray) -> HoldoutMetrics:
    y_prob = np.clip(y_prob, 1e-6, 1 - 1e-6)
    return HoldoutMetrics(
        name=name,
        log_loss=float(log_loss(y_true, y_prob)),
        brier=float(brier_score_loss(y_true, y_prob)),
        accuracy=float(accuracy_score(y_true, y_prob >= 0.5)),
    )


def constant_market_proxy_log_loss(
    y_true: np.ndarray, home_rate: float
) -> float:
    """No odds: constant home-win rate from training seasons as market proxy."""
    p = float(np.clip(home_rate, 1e-6, 1 - 1e-6))
    probs = np.full(len(y_true), p)
    return float(log_loss(y_true, probs))


def production_gate_passes(
    model_log_loss: float,
    naive_log_loss: float,
    market_proxy_log_loss: float,
) -> bool:
    return model_log_loss < naive_log_loss and model_log_loss <= market_proxy_log_loss


def train_logistic(
    train: pd.DataFrame,
    test: pd.DataFrame,
    feature_cols: list[str] | None = None,
    metrics_name: str = "logistic_regression_v1",
) -> tuple[Pipeline, HoldoutMetrics]:
    cols = feature_cols or FEATURE_COLUMNS
    model = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(max_iter=1000, random_state=42)),
        ]
    )
    model.fit(train[cols].values, train["home_win"].values)
    prob = model.predict_proba(test[cols].values)[:, 1]
    metrics = compute_metrics(metrics_name, test["home_win"].values, prob)
    return model, metrics


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_active_manifest() -> dict[str, Any] | None:
    if not ACTIVE_NBA_MANIFEST.exists():
        return None
    return json.loads(ACTIVE_NBA_MANIFEST.read_text(encoding="utf-8"))


def feature_columns_for_set(feature_set: str | None) -> list[str]:
    if feature_set == FEATURE_SET_V2:
        return list(FEATURE_COLUMNS_WAVE2)
    if feature_set in (FEATURE_SET_V1, "v1_logistic"):
        return list(FEATURE_COLUMNS_V1)
    return list(FEATURE_COLUMNS)


def save_nba_promotion(
    run_id: str,
    artifact: dict[str, Any],
    *,
    feature_set: str,
) -> dict[str, Any]:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    versioned = MODELS_DIR / f"{run_id}.joblib"
    joblib.dump(artifact, versioned)
    joblib.dump(artifact, MODEL_ARTIFACT)
    manifest = {
        "track": "nba_moneyline",
        "run_id": run_id,
        "path": MODEL_ARTIFACT.relative_to(PROJECT_ROOT).as_posix(),
        "feature_set": feature_set,
        "model_version": artifact["model_version"],
        "promoted_at": _iso_now(),
    }
    ACTIVE_NBA_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    ACTIVE_NBA_MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def load_model_artifact() -> dict[str, Any]:
    if ACTIVE_NBA_MANIFEST.exists():
        manifest = json.loads(ACTIVE_NBA_MANIFEST.read_text(encoding="utf-8"))
        path = PROJECT_ROOT / manifest["path"]
        if path.exists():
            artifact = joblib.load(path)
            artifact.setdefault("feature_set", manifest.get("feature_set"))
            return artifact
    if MODEL_ARTIFACT.exists():
        return joblib.load(MODEL_ARTIFACT)
    raise FileNotFoundError(
        f"No NBA model at {MODEL_ARTIFACT}. Run scripts/train_nba_baseline.py first."
    )


def predict_home_win_proba(df: pd.DataFrame) -> np.ndarray:
    from app.features.nba_pregame import build_features_for_slate

    artifact = load_model_artifact()
    manifest = load_active_manifest()
    feature_set = (manifest or {}).get("feature_set") or artifact.get("feature_set")
    cols = feature_columns_for_set(feature_set)
    if artifact.get("feature_columns"):
        cols = list(artifact["feature_columns"])
    rest_fill = float(artifact.get("rest_fill", DEFAULT_REST_FILL))
    pts_fill = float(artifact.get("pts_fill", DEFAULT_PTS_FILL))
    if "home_last10_win_pct" not in df.columns:
        prepared = build_features_for_slate(df, rest_fill=rest_fill, pts_fill=pts_fill)
    else:
        prepared = df.copy()
        if "elo_home_pre" not in prepared.columns:
            prepared = attach_elo_for_slate(prepared)
    return artifact["model"].predict_proba(prepared[cols].values)[:, 1]


def _metrics_dict(m: HoldoutMetrics) -> dict[str, float]:
    return {"log_loss": m.log_loss, "brier": m.brier, "accuracy": m.accuracy}


def run_training() -> dict[str, Any]:
    raw = load_games()
    feat_all = build_features_for_history(raw)
    train, test = time_split(feat_all)

    rest_fill = float(
        pd.concat([train["home_rest_days"], train["away_rest_days"]]).median()
    )
    if math.isnan(rest_fill):
        rest_fill = DEFAULT_REST_FILL
    pts_fill = float(
        pd.concat([train["home_score"], train["away_score"]]).median()
    )
    if math.isnan(pts_fill):
        pts_fill = DEFAULT_PTS_FILL

    y_test = test["home_win"].values
    train_raw, test_raw = time_split(raw)
    home_rate = float(train_raw["home_win"].mean())

    full_for_elo = feat_all.copy()
    n_train = len(train)
    elo_probs = predict_elo(full_for_elo)[n_train:]

    home_rate_probs = predict_home_rate_constant(train_raw, test_raw)
    rolling_probs = predict_rolling_naive(test)

    v1_model, v1_metrics = train_logistic(
        train, test, FEATURE_COLUMNS_V1, "logistic_regression_v1"
    )
    v2_model, v2_metrics = train_logistic(
        train, test, FEATURE_COLUMNS_WAVE2, "logistic_regression_v2_score"
    )

    home_rate_metrics = compute_metrics(
        "naive_home_win_rate", y_test, home_rate_probs
    )
    elo_metrics = compute_metrics("elo_baseline", y_test, elo_probs)
    rolling_metrics = compute_metrics("rolling_last10_home", y_test, rolling_probs)

    market_proxy_ll = constant_market_proxy_log_loss(y_test, home_rate)
    naive_ll = min(home_rate_metrics.log_loss, elo_metrics.log_loss)
    v1_gate_passes = production_gate_passes(
        v1_metrics.log_loss, naive_ll, market_proxy_ll
    )
    v2_gate_passes = production_gate_passes(
        v2_metrics.log_loss, naive_ll, market_proxy_ll
    )

    promote_v2 = (
        v2_metrics.log_loss < v1_metrics.log_loss and v2_gate_passes
    )

    if promote_v2:
        production_model = "v2_score_rolling"
        production_feature_set = FEATURE_SET_V2
        promoted_cols = FEATURE_COLUMNS_WAVE2
        promoted_artifact_model = v2_model
        promoted_metrics = v2_metrics
        gate_passes = v2_gate_passes
    else:
        production_model = "v1_logistic"
        production_feature_set = FEATURE_SET_V1
        promoted_cols = FEATURE_COLUMNS_V1
        promoted_artifact_model = v1_model
        promoted_metrics = v1_metrics
        gate_passes = v1_gate_passes

    artifact = {
        "model": promoted_artifact_model,
        "model_version": production_model,
        "feature_set": production_feature_set,
        "feature_columns": list(promoted_cols),
        "rest_fill": rest_fill,
        "pts_fill": pts_fill,
        "train_seasons": list(TRAIN_SEASONS),
        "holdout_season": HOLDOUT_SEASON,
        "train_home_win_rate": home_rate,
    }
    MODEL_ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    save_nba_promotion(
        "train_nba_baseline", artifact, feature_set=production_feature_set
    )

    results: dict[str, Any] = {
        "train_seasons": list(TRAIN_SEASONS),
        "holdout_season": HOLDOUT_SEASON,
        "train_rows": len(train),
        "holdout_rows": len(test),
        "feature_set": FEATURE_SET_V2,
        "production_model": production_model,
        "promoted_v2": promote_v2,
        "imputation": {
            "last10_win_pct": NEUTRAL_LAST10_WIN_PCT,
            "season_win_pct": NEUTRAL_SEASON_WIN_PCT,
            "rest_days": rest_fill,
            "pts_per_game": pts_fill,
            "margin_avg": 0.0,
        },
        "v1_comparison": {
            "feature_set": FEATURE_SET_V1,
            "holdout": _metrics_dict(v1_metrics),
            "production_gate_passes": v1_gate_passes,
        },
        "v2_comparison": {
            "feature_set": FEATURE_SET_V2,
            "holdout": _metrics_dict(v2_metrics),
            "production_gate_passes": v2_gate_passes,
            "beats_v1_log_loss": v2_metrics.log_loss < v1_metrics.log_loss,
        },
        "metrics": {
            m.name: _metrics_dict(m)
            for m in (
                v1_metrics,
                v2_metrics,
                home_rate_metrics,
                elo_metrics,
                rolling_metrics,
            )
        },
        "market_proxy": {
            "log_loss": market_proxy_ll,
            "home_win_rate": home_rate,
            "source": "train_constant_no_odds",
        },
        "phase_gate": {
            "rule": "log_loss < best_naive AND log_loss <= market_proxy",
            "best_naive_log_loss": naive_ll,
            "market_proxy_log_loss": market_proxy_ll,
            "passes": gate_passes,
            "active_model": production_model,
        },
        "active_holdout": _metrics_dict(promoted_metrics),
    }
    METRICS_JSON.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results


def format_metrics_table(results: dict[str, Any]) -> str:
    lines = [
        "| Model | Log loss | Brier | Accuracy |",
        "|-------|----------|-------|----------|",
    ]
    for name, m in results["metrics"].items():
        lines.append(
            f"| {name} | {m['log_loss']:.4f} | {m['brier']:.4f} | {m['accuracy']:.3f} |"
        )
    proxy = results.get("market_proxy", {})
    if proxy.get("log_loss") is not None:
        lines.append(
            f"| market_proxy (constant) | {proxy['log_loss']:.4f} | — | — |"
        )
    return "\n".join(lines)
