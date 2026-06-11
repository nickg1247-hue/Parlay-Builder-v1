"""CFB home_win baseline: logistic + Platt calibration + Elo comparison."""

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
from app.features.cfb_pregame import FEATURE_COLUMNS, build_features_for_history
from app.models.platt_calibration import PlattCalibrator

MODEL_ARTIFACT = PROJECT_ROOT / "data" / "processed" / "cfb_baseline_model.joblib"
METRICS_JSON = PROJECT_ROOT / "data" / "processed" / "cfb_baseline_metrics.json"
MODELS_DIR = PROJECT_ROOT / "data" / "processed" / "models"
ACTIVE_CFB_MANIFEST = PROJECT_ROOT / "data" / "processed" / "active_cfb_model.json"
PARQUET_PATH = PROJECT_ROOT / "data" / "processed" / "cfb_games.parquet"

BASE_TRAIN_SEASONS = (2022, 2023)
PLATT_SEASON = 2024
HOLDOUT_SEASON = 2025
REGRESSION_TRAIN_SEASONS = (2022, 2023, 2024)
FEATURE_SET = "cfb_v1"

DEFAULT_REST_FILL = 7.0

ELO_START = 1500.0
ELO_K = 20.0
ELO_HOME_ADV = 55.0


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
            df = pd.read_sql("SELECT * FROM cfb_games ORDER BY date, game_id", conn)
        finally:
            conn.close()
    if df.empty:
        raise FileNotFoundError(
            f"No CFB games at {PARQUET_PATH}. Run scripts/bootstrap_cfb.py first."
        )
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values(["date", "game_id"]).reset_index(drop=True)


def time_split_regression(
    df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Train 2022–2024, holdout 2025 — used by spread/totals GBR models."""
    train = df[df["season"].isin(REGRESSION_TRAIN_SEASONS)].copy()
    holdout = df[df["season"] == HOLDOUT_SEASON].copy()
    if train.empty or holdout.empty:
        raise ValueError(
            f"Expected regression train {REGRESSION_TRAIN_SEASONS}, "
            f"holdout {HOLDOUT_SEASON}"
        )
    return train, holdout


def time_split_base_platt_holdout(
    df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    base = df[df["season"].isin(BASE_TRAIN_SEASONS)].copy()
    platt = df[df["season"] == PLATT_SEASON].copy()
    holdout = df[df["season"] == HOLDOUT_SEASON].copy()
    if base.empty or platt.empty or holdout.empty:
        raise ValueError(
            f"Expected base {BASE_TRAIN_SEASONS}, Platt {PLATT_SEASON}, "
            f"holdout {HOLDOUT_SEASON}"
        )
    return base, platt, holdout


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


def compute_metrics(name: str, y_true: np.ndarray, y_prob: np.ndarray) -> HoldoutMetrics:
    y_prob = np.clip(y_prob, 1e-6, 1 - 1e-6)
    return HoldoutMetrics(
        name=name,
        log_loss=float(log_loss(y_true, y_prob)),
        brier=float(brier_score_loss(y_true, y_prob)),
        accuracy=float(accuracy_score(y_true, y_prob >= 0.5)),
    )


def production_gate_passes(model_log_loss: float, naive_log_loss: float) -> bool:
    return model_log_loss < naive_log_loss


def train_logistic(
    train: pd.DataFrame,
    feature_cols: list[str] | None = None,
) -> Pipeline:
    cols = feature_cols or FEATURE_COLUMNS
    model = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(max_iter=1000, random_state=42)),
        ]
    )
    model.fit(train[cols].values, train["home_win"].values)
    return model


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_active_manifest() -> dict[str, Any] | None:
    if not ACTIVE_CFB_MANIFEST.exists():
        return None
    return json.loads(ACTIVE_CFB_MANIFEST.read_text(encoding="utf-8"))


def _manifest_path(path: Path) -> str:
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def save_cfb_promotion(
    run_id: str,
    artifact: dict[str, Any],
) -> dict[str, Any]:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    versioned = MODELS_DIR / f"{run_id}.joblib"
    joblib.dump(artifact, versioned)
    joblib.dump(artifact, MODEL_ARTIFACT)
    manifest = {
        "track": "cfb_moneyline",
        "run_id": run_id,
        "path": _manifest_path(MODEL_ARTIFACT),
        "feature_set": FEATURE_SET,
        "model_version": artifact["model_version"],
        "promoted_at": _iso_now(),
    }
    ACTIVE_CFB_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    ACTIVE_CFB_MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def load_model_artifact() -> dict[str, Any]:
    if ACTIVE_CFB_MANIFEST.exists():
        manifest = json.loads(ACTIVE_CFB_MANIFEST.read_text(encoding="utf-8"))
        path = Path(manifest["path"])
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if path.exists():
            return joblib.load(path)
    if MODEL_ARTIFACT.exists():
        return joblib.load(MODEL_ARTIFACT)
    raise FileNotFoundError(
        f"No CFB model at {MODEL_ARTIFACT}. Run scripts/train_cfb_baseline.py first."
    )


def predict_home_win_proba(df: pd.DataFrame) -> np.ndarray:
    from app.features.cfb_pregame import build_features_for_slate

    artifact = load_model_artifact()
    cols = list(artifact.get("feature_columns") or FEATURE_COLUMNS)
    rest_fill = float(artifact.get("rest_fill", DEFAULT_REST_FILL))
    prepared = build_features_for_slate(df, rest_fill=rest_fill)
    raw = artifact["model"].predict_proba(prepared[cols].values)[:, 1]
    platt = artifact.get("platt_calibrator")
    if platt is not None:
        raw = platt.transform(raw)
    if len(prepared) != len(df):
        by_id = dict(zip(prepared["game_id"].astype(str), raw))
        return df["game_id"].astype(str).map(by_id).to_numpy()
    return raw


def _metrics_dict(m: HoldoutMetrics) -> dict[str, float]:
    return {"log_loss": m.log_loss, "brier": m.brier, "accuracy": m.accuracy}


def run_training() -> dict[str, Any]:
    raw = load_games()
    feat_all = build_features_for_history(raw)
    base, platt_df, holdout = time_split_base_platt_holdout(feat_all)

    rest_fill = float(
        pd.concat([base["home_rest_days"], base["away_rest_days"]]).median()
    )
    if math.isnan(rest_fill):
        rest_fill = DEFAULT_REST_FILL

    y_holdout = holdout["home_win"].values
    train_raw = raw[raw["season"].isin(BASE_TRAIN_SEASONS)]
    home_rate = float(train_raw["home_win"].mean())

    base_model = train_logistic(base)
    raw_platt_train = base_model.predict_proba(base[FEATURE_COLUMNS].values)[:, 1]
    raw_platt_cal = base_model.predict_proba(platt_df[FEATURE_COLUMNS].values)[:, 1]

    platt = PlattCalibrator()
    platt.fit(raw_platt_cal, platt_df["home_win"].values)

    raw_holdout = base_model.predict_proba(holdout[FEATURE_COLUMNS].values)[:, 1]
    cal_holdout = platt.transform(raw_holdout)

    logistic_metrics = compute_metrics(
        "logistic_regression_v1", y_holdout, cal_holdout
    )
    raw_logistic_metrics = compute_metrics(
        "logistic_uncalibrated", y_holdout, raw_holdout
    )

    full_for_elo = feat_all.copy()
    n_before_holdout = len(feat_all[feat_all["season"] != HOLDOUT_SEASON])
    elo_probs = predict_elo(full_for_elo)[n_before_holdout:]
    home_rate_probs = predict_home_rate_constant(train_raw, holdout)
    home_rate_metrics = compute_metrics("naive_home_win_rate", y_holdout, home_rate_probs)
    elo_metrics = compute_metrics("elo_baseline", y_holdout, elo_probs)

    naive_ll = min(home_rate_metrics.log_loss, elo_metrics.log_loss)
    gate_passes = production_gate_passes(logistic_metrics.log_loss, naive_ll)

    artifact = {
        "model": base_model,
        "platt_calibrator": platt,
        "model_version": "v1_logistic_platt",
        "feature_set": FEATURE_SET,
        "feature_columns": list(FEATURE_COLUMNS),
        "rest_fill": rest_fill,
        "base_train_seasons": list(BASE_TRAIN_SEASONS),
        "platt_season": PLATT_SEASON,
        "holdout_season": HOLDOUT_SEASON,
        "train_home_win_rate": home_rate,
    }
    MODEL_ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    save_cfb_promotion("train_cfb_baseline", artifact)

    results: dict[str, Any] = {
        "base_train_seasons": list(BASE_TRAIN_SEASONS),
        "platt_season": PLATT_SEASON,
        "holdout_season": HOLDOUT_SEASON,
        "train_rows": len(base) + len(platt_df),
        "holdout_rows": len(holdout),
        "feature_set": FEATURE_SET,
        "production_model": "v1_logistic_platt",
        "imputation": {"rest_days": rest_fill},
        "metrics": {
            m.name: _metrics_dict(m)
            for m in (
                logistic_metrics,
                raw_logistic_metrics,
                home_rate_metrics,
                elo_metrics,
            )
        },
        "phase_gate": {
            "rule": "holdout log_loss < best_naive (home rate or Elo)",
            "best_naive_log_loss": naive_ll,
            "passes": gate_passes,
            "active_model": "v1_logistic_platt",
        },
        "active_holdout": _metrics_dict(logistic_metrics),
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
    return "\n".join(lines)
