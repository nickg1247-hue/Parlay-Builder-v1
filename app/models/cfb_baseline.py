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
from app.features.cfb_pregame import (
    FEATURE_COLUMNS,
    FEATURE_COLUMNS_V1,
    FEATURE_COLUMNS_V2,
    FEATURE_COLUMNS_V3,
    build_features_for_history,
)
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
FEATURE_SET_V1 = "cfb_v1"
FEATURE_SET_V2 = "cfb_v2"
FEATURE_SET_V3 = "cfb_v3"
FEATURE_SET = FEATURE_SET_V3

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
    for col, default in (
        ("neutral_site", 0),
        ("conference_game", 0),
        ("home_conference", ""),
        ("away_conference", ""),
        ("week", 0),
    ):
        if col not in df.columns:
            df[col] = default
        else:
            df[col] = df[col].fillna(default)
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


def _elo_expected(home_elo: float, away_elo: float, *, neutral: bool = False) -> float:
    adv = 0.0 if neutral else ELO_HOME_ADV
    return 1.0 / (1.0 + 10 ** ((away_elo - home_elo - adv) / 400.0))


def _elo_update(
    home_elo: float,
    away_elo: float,
    home_win: int,
    *,
    neutral: bool = False,
) -> tuple[float, float]:
    expected = _elo_expected(home_elo, away_elo, neutral=neutral)
    actual = float(home_win)
    home_elo += ELO_K * (actual - expected)
    away_elo += ELO_K * ((1.0 - actual) - (1.0 - expected))
    return home_elo, away_elo


def _row_neutral(row, games_df: pd.DataFrame | None, idx: int) -> bool:
    if games_df is not None and "neutral_site" in games_df.columns:
        val = games_df.iloc[idx]["neutral_site"]
        return int(val) == 1 if pd.notna(val) else False
    if hasattr(row, "neutral_site") and pd.notna(getattr(row, "neutral_site", None)):
        return int(row.neutral_site) == 1
    return False


def attach_elo_features(
    df: pd.DataFrame,
    *,
    update_ratings: bool = True,
    games_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    out = df.copy()
    ratings: dict[str, float] = {}
    home_elos: list[float] = []
    away_elos: list[float] = []
    for i, row in enumerate(out.itertuples(index=False)):
        home_elos.append(ratings.get(row.home_team, ELO_START))
        away_elos.append(ratings.get(row.away_team, ELO_START))
        hw = getattr(row, "home_win", None)
        if update_ratings and hw is not None and pd.notna(hw):
            neutral = _row_neutral(row, games_df, i)
            home, away = _elo_update(
                ratings.get(row.home_team, ELO_START),
                ratings.get(row.away_team, ELO_START),
                int(hw),
                neutral=neutral,
            )
            ratings[row.home_team] = home
            ratings[row.away_team] = away
    out["elo_home_pre"] = home_elos
    out["elo_away_pre"] = away_elos
    return out


def current_elo_ratings(history: pd.DataFrame) -> dict[str, float]:
    ratings: dict[str, float] = {}
    hist = history.sort_values(["date", "game_id"]).reset_index(drop=True)
    for i, row in enumerate(hist.itertuples(index=False)):
        neutral = _row_neutral(row, hist, i)
        home, away = _elo_update(
            ratings.get(row.home_team, ELO_START),
            ratings.get(row.away_team, ELO_START),
            int(row.home_win),
            neutral=neutral,
        )
        ratings[row.home_team] = home
        ratings[row.away_team] = away
    return ratings


def attach_elo_for_slate(
    df: pd.DataFrame,
    history: pd.DataFrame | None = None,
    *,
    games_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    del games_df
    hist = history if history is not None else load_games()
    out = df.copy()
    min_date = pd.to_datetime(out["date"]).min()
    prior = hist[hist["date"] < min_date]
    ratings = current_elo_ratings(prior) if not prior.empty else {}
    out["elo_home_pre"] = [ratings.get(t, ELO_START) for t in out["home_team"]]
    out["elo_away_pre"] = [ratings.get(t, ELO_START) for t in out["away_team"]]
    return out


def predict_elo(df: pd.DataFrame, *, games_df: pd.DataFrame | None = None) -> np.ndarray:
    ratings: dict[str, float] = {}
    probs: list[float] = []
    source = games_df if games_df is not None else df
    for i, row in enumerate(df.itertuples(index=False)):
        home = ratings.get(row.home_team, ELO_START)
        away = ratings.get(row.away_team, ELO_START)
        neutral = _row_neutral(row, source, i)
        probs.append(_elo_expected(home, away, neutral=neutral))
        home, away = _elo_update(home, away, int(row.home_win), neutral=neutral)
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
        "feature_set": artifact["feature_set"],
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


def _train_calibrated_holdout(
    base: pd.DataFrame,
    platt_df: pd.DataFrame,
    holdout: pd.DataFrame,
    cols: list[str],
) -> tuple[Pipeline, PlattCalibrator, np.ndarray, np.ndarray]:
    base_model = train_logistic(base, cols)
    platt = PlattCalibrator()
    raw_platt = base_model.predict_proba(platt_df[cols].values)[:, 1]
    platt.fit(raw_platt, platt_df["home_win"].values)
    raw_holdout = base_model.predict_proba(holdout[cols].values)[:, 1]
    cal_holdout = platt.transform(raw_holdout)
    return base_model, platt, cal_holdout, raw_holdout


def run_training() -> dict[str, Any]:
    raw = load_games()
    try:
        from app.ingest.cfb_sp_plus import ensure_sp_plus_cache

        seasons = tuple(sorted(int(s) for s in raw["season"].unique()))
        ensure_sp_plus_cache(seasons)
    except SystemExit:
        raise
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning("SP+ cache warm skipped: %s", exc)

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

    v1_model, v1_platt, v1_cal_holdout, _v1_raw_holdout = _train_calibrated_holdout(
        base, platt_df, holdout, FEATURE_COLUMNS_V1
    )
    v2_model, v2_platt, v2_cal_holdout, v2_raw_holdout = _train_calibrated_holdout(
        base, platt_df, holdout, FEATURE_COLUMNS_V2
    )
    v3_model, v3_platt, v3_cal_holdout, v3_raw_holdout = _train_calibrated_holdout(
        base, platt_df, holdout, FEATURE_COLUMNS_V3
    )

    v1_metrics = compute_metrics("logistic_regression_v1", y_holdout, v1_cal_holdout)
    v2_metrics = compute_metrics("logistic_regression_v2", y_holdout, v2_cal_holdout)
    v3_metrics = compute_metrics("logistic_regression_v3", y_holdout, v3_cal_holdout)
    raw_v2_metrics = compute_metrics("v2_uncalibrated", y_holdout, v2_raw_holdout)
    raw_v3_metrics = compute_metrics("v3_uncalibrated", y_holdout, v3_raw_holdout)

    full_for_elo = feat_all.copy()
    n_before_holdout = len(feat_all[feat_all["season"] != HOLDOUT_SEASON])
    elo_probs = predict_elo(full_for_elo, games_df=raw)[n_before_holdout:]
    home_rate_probs = predict_home_rate_constant(train_raw, holdout)
    home_rate_metrics = compute_metrics("naive_home_win_rate", y_holdout, home_rate_probs)
    elo_metrics = compute_metrics("elo_baseline", y_holdout, elo_probs)

    naive_ll = min(home_rate_metrics.log_loss, elo_metrics.log_loss)
    v1_gate = production_gate_passes(v1_metrics.log_loss, naive_ll)
    v2_beats_v1 = v2_metrics.log_loss < v1_metrics.log_loss
    v2_gate = production_gate_passes(v2_metrics.log_loss, naive_ll)
    v3_beats_v2 = v3_metrics.log_loss < v2_metrics.log_loss
    v3_gate = production_gate_passes(v3_metrics.log_loss, naive_ll)
    promote_v3 = v3_beats_v2 and v3_gate
    promote_v2 = v2_beats_v1 and v2_gate and not promote_v3

    market_eval_summary: dict[str, Any] | None = None
    market_v3_advisory: bool | None = None
    if promote_v3:
        try:
            temp_v3_artifact = {
                "model": v3_model,
                "platt_calibrator": v3_platt,
                "model_version": "v3_logistic_platt",
                "feature_set": FEATURE_SET_V3,
                "feature_columns": list(FEATURE_COLUMNS_V3),
                "rest_fill": rest_fill,
                "base_train_seasons": list(BASE_TRAIN_SEASONS),
                "platt_season": PLATT_SEASON,
                "holdout_season": HOLDOUT_SEASON,
                "train_home_win_rate": home_rate,
            }
            MODEL_ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
            joblib.dump(temp_v3_artifact, MODEL_ARTIFACT)
            from app.odds.cfb_market_eval import run_market_evaluation

            market_eval_summary = run_market_evaluation()
            market_v3_advisory = market_eval_summary.get("model_beats_market_log_loss")
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("v3 market eval skipped: %s", exc)

    if promote_v3:
        prod_model = v3_model
        prod_platt = v3_platt
        prod_version = "v3_logistic_platt"
        prod_set = FEATURE_SET_V3
        prod_cols = list(FEATURE_COLUMNS_V3)
        prod_metrics = v3_metrics
        active_model = "v3_logistic_platt"
        gate_passes = v3_gate
        promoted_v2 = v2_beats_v1 and v2_gate
        promoted_v3 = True
    elif promote_v2:
        prod_model = v2_model
        prod_platt = v2_platt
        prod_version = "v2_logistic_platt"
        prod_set = FEATURE_SET_V2
        prod_cols = list(FEATURE_COLUMNS_V2)
        prod_metrics = v2_metrics
        active_model = "v2_logistic_platt"
        gate_passes = v2_gate
        promoted_v2 = True
        promoted_v3 = False
    else:
        prod_model = v1_model
        prod_platt = v1_platt
        prod_version = "v1_logistic_platt"
        prod_set = FEATURE_SET_V1
        prod_cols = list(FEATURE_COLUMNS_V1)
        prod_metrics = v1_metrics
        active_model = "v1_logistic_platt"
        gate_passes = v1_gate
        promoted_v2 = False
        promoted_v3 = False

    artifact = {
        "model": prod_model,
        "platt_calibrator": prod_platt,
        "model_version": prod_version,
        "feature_set": prod_set,
        "feature_columns": prod_cols,
        "rest_fill": rest_fill,
        "base_train_seasons": list(BASE_TRAIN_SEASONS),
        "platt_season": PLATT_SEASON,
        "holdout_season": HOLDOUT_SEASON,
        "train_home_win_rate": home_rate,
    }
    MODEL_ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    save_cfb_promotion("train_cfb_baseline", artifact)

    v2_fail_reason = None
    if not promote_v2:
        if not v2_beats_v1:
            v2_fail_reason = f"v2 log loss {v2_metrics.log_loss:.4f} >= v1 {v1_metrics.log_loss:.4f}"
        elif not v2_gate:
            v2_fail_reason = f"v2 log loss {v2_metrics.log_loss:.4f} >= naive {naive_ll:.4f}"

    v3_fail_reason = None
    if not promote_v3:
        if not v3_beats_v2:
            v3_fail_reason = f"v3 log loss {v3_metrics.log_loss:.4f} >= v2 {v2_metrics.log_loss:.4f}"
        elif not v3_gate:
            v3_fail_reason = f"v3 log loss {v3_metrics.log_loss:.4f} >= naive {naive_ll:.4f}"

    results: dict[str, Any] = {
        "base_train_seasons": list(BASE_TRAIN_SEASONS),
        "platt_season": PLATT_SEASON,
        "holdout_season": HOLDOUT_SEASON,
        "train_rows": len(base) + len(platt_df),
        "holdout_rows": len(holdout),
        "feature_set": prod_set,
        "production_model": prod_version,
        "promoted_v2": promoted_v2,
        "promoted_v3": promoted_v3,
        "active_model": active_model,
        "imputation": {"rest_days": rest_fill},
        "v1_comparison": {
            "feature_set": FEATURE_SET_V1,
            "holdout": _metrics_dict(v1_metrics),
            "gate_passes": v1_gate,
        },
        "v2_comparison": {
            "feature_set": FEATURE_SET_V2,
            "holdout": _metrics_dict(v2_metrics),
            "gate_passes": v2_gate,
            "beats_v1": v2_beats_v1,
            "fail_reason": v2_fail_reason,
        },
        "v3_comparison": {
            "feature_set": FEATURE_SET_V3,
            "holdout": _metrics_dict(v3_metrics),
            "gate_passes": v3_gate,
            "beats_v2": v3_beats_v2,
            "fail_reason": v3_fail_reason,
        },
        "metrics": {
            m.name: _metrics_dict(m)
            for m in (
                v1_metrics,
                v2_metrics,
                v3_metrics,
                raw_v2_metrics,
                raw_v3_metrics,
                home_rate_metrics,
                elo_metrics,
            )
        },
        "phase_gate": {
            "rule": "Promote v3 if holdout LL beats v2 and naive; else v2 if beats v1; else v1. Market eval is advisory only.",
            "best_naive_log_loss": naive_ll,
            "passes": gate_passes,
            "active_model": active_model,
            "market_v3_beats_market": market_v3_advisory,
            "market_eval": market_eval_summary,
        },
        "active_holdout": _metrics_dict(prod_metrics),
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
