"""CFB point-spread margin model — GBR + Normal CDF cover probs."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import log_loss, mean_absolute_error

from app.config import PROJECT_ROOT
from app.features.cfb_pregame import (
    DEFAULT_PTS_FILL,
    DEFAULT_REST_FILL,
    MARGIN_FEATURE_COLUMNS,
    build_margin_features_for_history,
    build_margin_features_for_slate,
)
from app.models.cfb_baseline import (
    HOLDOUT_SEASON,
    METRICS_JSON as CFB_BASELINE_METRICS_JSON,
    REGRESSION_TRAIN_SEASONS,
    load_games,
    time_split_regression,
)
from app.odds.spread_math import (
    model_prob_away_cover,
    model_prob_home_cover,
    norm_cdf,
    side_covers,
)

MODEL_ARTIFACT = PROJECT_ROOT / "data" / "processed" / "cfb_margin_model.joblib"
METRICS_JSON = PROJECT_ROOT / "data" / "processed" / "cfb_margin_metrics.json"
ACTIVE_MARGIN_MANIFEST = (
    PROJECT_ROOT / "data" / "processed" / "active_cfb_margin_model.json"
)

PROXY_HOME_SPREAD = -7.0
PROXY_AWAY_SPREAD = 7.0
DEFAULT_MARGIN_STD = 14.0
FEATURE_COLUMNS = list(MARGIN_FEATURE_COLUMNS)

COIN_FLIP_LOG_LOSS = math.log(2.0)
MAE_GATE_MAX = 18.0
ML_LOG_LOSS_TOLERANCE = 0.01


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def margin_production_gate_passes(metrics: dict[str, Any]) -> bool:
    mae = float(metrics.get("holdout_mae_margin", 999.0))
    home_ll = float(metrics.get("proxy_cover_log_loss_home", 999.0))
    away_ll = float(metrics.get("proxy_cover_log_loss_away", 999.0))
    if mae >= MAE_GATE_MAX:
        return False
    if home_ll >= COIN_FLIP_LOG_LOSS or away_ll >= COIN_FLIP_LOG_LOSS:
        return False
    return True


def _binary_log_loss(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    y_prob = np.clip(y_prob, 1e-6, 1 - 1e-6)
    return float(log_loss(y_true, y_prob, labels=[0, 1]))


def _cfb_baseline_holdout_log_loss() -> float | None:
    if not CFB_BASELINE_METRICS_JSON.exists():
        return None
    try:
        saved = json.loads(CFB_BASELINE_METRICS_JSON.read_text(encoding="utf-8"))
        active = saved.get("active_holdout", {})
        if active.get("log_loss") is not None:
            return float(active["log_loss"])
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        pass
    return None


def run_training() -> dict[str, Any]:
    raw = load_games()
    feat = build_margin_features_for_history(raw)
    merge_cols = ["game_id", "home_score", "away_score"]
    if "home_win" not in feat.columns:
        merge_cols.append("home_win")
    feat = feat.merge(raw[merge_cols], on="game_id", how="inner")
    feat = feat[feat["home_score"].notna() & feat["away_score"].notna()].copy()
    feat["margin"] = feat["home_score"].astype(float) - feat["away_score"].astype(float)

    train, test = time_split_regression(feat)

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

    reg = GradientBoostingRegressor(
        n_estimators=120,
        max_depth=3,
        learning_rate=0.08,
        random_state=42,
    )
    reg.fit(train[FEATURE_COLUMNS].values, train["margin"].values)
    pred_test = reg.predict(test[FEATURE_COLUMNS].values)
    residuals = test["margin"].values - pred_test
    margin_std = float(np.std(residuals, ddof=1))
    if math.isnan(margin_std) or margin_std <= 0:
        margin_std = DEFAULT_MARGIN_STD

    mae = float(mean_absolute_error(test["margin"], pred_test))

    proxy_home_actual = np.array(
        [
            int(
                side_covers(
                    "home",
                    r.home_score,
                    r.away_score,
                    PROXY_HOME_SPREAD,
                    PROXY_AWAY_SPREAD,
                )
            )
            for r in test.itertuples(index=False)
        ]
    )
    proxy_away_actual = np.array(
        [
            int(
                side_covers(
                    "away",
                    r.home_score,
                    r.away_score,
                    PROXY_HOME_SPREAD,
                    PROXY_AWAY_SPREAD,
                )
            )
            for r in test.itertuples(index=False)
        ]
    )
    proxy_home_probs = np.array(
        [
            model_prob_home_cover(float(p), margin_std, PROXY_HOME_SPREAD)
            for p in pred_test
        ]
    )
    proxy_away_probs = np.array(
        [
            model_prob_away_cover(float(p), margin_std, PROXY_AWAY_SPREAD)
            for p in pred_test
        ]
    )
    proxy_home_ll = _binary_log_loss(proxy_home_actual, proxy_home_probs)
    proxy_away_ll = _binary_log_loss(proxy_away_actual, proxy_away_probs)

    margin_ml_probs = np.array(
        [1.0 - norm_cdf(0.0, float(p), margin_std) for p in pred_test]
    )
    margin_ml_probs = np.clip(margin_ml_probs, 1e-6, 1 - 1e-6)
    margin_derived_ml_ll = _binary_log_loss(test["home_win"].values, margin_ml_probs)

    baseline_ll = _cfb_baseline_holdout_log_loss()
    beats_baseline = (
        baseline_ll is not None and margin_derived_ml_ll < baseline_ll + ML_LOG_LOSS_TOLERANCE
    )

    artifact = {
        "model": reg,
        "model_version": "v1_margin_gbr_normal",
        "feature_columns": FEATURE_COLUMNS,
        "feature_set": "cfb_margin_v1",
        "margin_std": margin_std,
        "rest_fill": rest_fill,
        "pts_fill": pts_fill,
        "proxy_lines": {
            "home_spread_point": PROXY_HOME_SPREAD,
            "away_spread_point": PROXY_AWAY_SPREAD,
        },
    }
    MODEL_ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, MODEL_ARTIFACT)

    gate_passes = margin_production_gate_passes(
        {
            "holdout_mae_margin": mae,
            "proxy_cover_log_loss_home": proxy_home_ll,
            "proxy_cover_log_loss_away": proxy_away_ll,
        }
    )

    manifest = {
        "track": "cfb_spread",
        "model_version": artifact["model_version"],
        "path": "data/processed/cfb_margin_model.joblib",
        "feature_set": artifact["feature_set"],
        "production_ready": gate_passes,
        "promoted_at": _iso_now(),
    }
    ACTIVE_MARGIN_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    ACTIVE_MARGIN_MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    results: dict[str, Any] = {
        "train_seasons": list(REGRESSION_TRAIN_SEASONS),
        "holdout_season": HOLDOUT_SEASON,
        "train_rows": len(train),
        "holdout_rows": len(test),
        "feature_set": artifact["feature_set"],
        "holdout_mae_margin": round(mae, 4),
        "holdout_margin_std": round(margin_std, 4),
        "proxy_cover_log_loss_home": round(proxy_home_ll, 4),
        "proxy_cover_log_loss_away": round(proxy_away_ll, 4),
        "margin_derived_ml_log_loss": round(margin_derived_ml_ll, 4),
        "cfb_baseline_log_loss": round(baseline_ll, 4) if baseline_ll else None,
        "beats_cfb_baseline_sanity": beats_baseline,
        "margin_production_gate_passes": gate_passes,
        "board_spread_enabled": gate_passes,
        "proxy_lines": artifact["proxy_lines"],
        "note": (
            "No free historical CFB spread CSV; holdout eval uses home -7 / away +7 proxy. "
            "Live board (Phase 3) uses book lines from Odds API americanfootball_ncaaf."
        ),
    }
    METRICS_JSON.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results


def load_margin_artifact() -> dict[str, Any]:
    if ACTIVE_MARGIN_MANIFEST.exists():
        manifest = json.loads(ACTIVE_MARGIN_MANIFEST.read_text(encoding="utf-8"))
        path = PROJECT_ROOT / manifest["path"]
        if path.exists():
            return joblib.load(path)
    if MODEL_ARTIFACT.exists():
        return joblib.load(MODEL_ARTIFACT)
    raise FileNotFoundError(
        f"No CFB margin model at {MODEL_ARTIFACT}. Run scripts/train_cfb_margin.py first."
    )


def load_margin_manifest() -> dict[str, Any] | None:
    if not ACTIVE_MARGIN_MANIFEST.exists():
        return None
    try:
        return json.loads(ACTIVE_MARGIN_MANIFEST.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def is_margin_production_ready() -> bool:
    manifest = load_margin_manifest()
    return bool(manifest and manifest.get("production_ready"))


def _prepare_features(df: pd.DataFrame) -> pd.DataFrame:
    if "home_season_pts_for" in df.columns and "elo_home_pre" in df.columns:
        return df.copy()
    artifact = load_margin_artifact()
    rest_fill = float(artifact.get("rest_fill", DEFAULT_REST_FILL))
    return build_margin_features_for_slate(df)


def predict_margin(df: pd.DataFrame) -> np.ndarray:
    artifact = load_margin_artifact()
    cols = artifact["feature_columns"]
    prepared = _prepare_features(df)
    return artifact["model"].predict(prepared[cols].values)


def predict_spread_covers(
    df: pd.DataFrame,
    *,
    home_spread_point: float | None = None,
    away_spread_point: float | None = None,
) -> pd.DataFrame:
    artifact = load_margin_artifact()
    cols = artifact["feature_columns"]
    std = float(artifact["margin_std"])
    proxy = artifact.get("proxy_lines") or {}
    hp_default = float(proxy.get("home_spread_point", PROXY_HOME_SPREAD))
    ap_default = float(proxy.get("away_spread_point", PROXY_AWAY_SPREAD))
    prepared = _prepare_features(df)
    margins = artifact["model"].predict(prepared[cols].values)
    if len(prepared) != len(df):
        margin_by_id = dict(zip(prepared["game_id"].astype(str), margins))
        margins = df["game_id"].astype(str).map(margin_by_id).to_numpy()
    out = df.copy()
    out["model_margin"] = margins

    home_probs: list[float | None] = []
    away_probs: list[float | None] = []
    for row, margin in zip(out.itertuples(index=False), margins):
        hp = getattr(row, "home_spread_point", None)
        ap = getattr(row, "away_spread_point", None)
        if hp is None or (isinstance(hp, float) and math.isnan(hp)):
            hp = home_spread_point if home_spread_point is not None else hp_default
        if ap is None or (isinstance(ap, float) and math.isnan(ap)):
            ap = away_spread_point if away_spread_point is not None else ap_default
        home_probs.append(round(model_prob_home_cover(float(margin), std, float(hp)), 4))
        away_probs.append(round(model_prob_away_cover(float(margin), std, float(ap)), 4))
    out["model_prob_home_cover"] = home_probs
    out["model_prob_away_cover"] = away_probs
    return out
