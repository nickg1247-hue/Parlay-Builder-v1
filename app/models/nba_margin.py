"""NBA point-spread margin model — GBR + Normal CDF cover probs."""

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
from app.features.nba_pregame import (
    FEATURE_COLUMNS_WAVE2,
    build_features_for_history,
    build_features_for_slate,
)
from app.models.nba_baseline import (
    DEFAULT_PTS_FILL,
    DEFAULT_REST_FILL,
    METRICS_JSON as NBA_BASELINE_METRICS_JSON,
    HOLDOUT_SEASON,
    TRAIN_SEASONS,
    load_games,
    production_gate_passes,
    time_split,
    train_logistic,
)
from app.odds.spread_math import (
    model_prob_away_cover,
    model_prob_home_cover,
    norm_cdf,
    side_covers,
)

MODEL_ARTIFACT = PROJECT_ROOT / "data" / "processed" / "nba_margin_model.joblib"
METRICS_JSON = PROJECT_ROOT / "data" / "processed" / "nba_margin_metrics.json"
ACTIVE_MARGIN_MANIFEST = (
    PROJECT_ROOT / "data" / "processed" / "active_nba_margin_model.json"
)

PROXY_HOME_SPREAD = -5.5
PROXY_AWAY_SPREAD = 5.5
DEFAULT_MARGIN_STD = 12.0
FEATURE_COLUMNS = list(FEATURE_COLUMNS_WAVE2)

COIN_FLIP_LOG_LOSS = math.log(2.0)
MAE_GATE_MAX = 15.0
V2_LOG_LOSS_TOLERANCE = 0.005


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def margin_production_gate_passes(metrics: dict[str, Any]) -> bool:
    """Sanity gate for spread board — independent of moneyline promotion."""
    mae = float(metrics.get("holdout_mae_margin", 999.0))
    home_ll = float(metrics.get("proxy_cover_log_loss_home", 999.0))
    away_ll = float(metrics.get("proxy_cover_log_loss_away", 999.0))
    margin_ml_ll = float(metrics.get("margin_derived_ml_log_loss", 999.0))
    v2_ll = float(metrics.get("v2_logistic_log_loss", 0.0))
    if mae >= MAE_GATE_MAX:
        return False
    if home_ll >= COIN_FLIP_LOG_LOSS or away_ll >= COIN_FLIP_LOG_LOSS:
        return False
    if margin_ml_ll > v2_ll + V2_LOG_LOSS_TOLERANCE:
        return False
    return True


def _binary_log_loss(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    y_prob = np.clip(y_prob, 1e-6, 1 - 1e-6)
    return float(log_loss(y_true, y_prob, labels=[0, 1]))


def _v2_logistic_holdout_log_loss(
    train: pd.DataFrame, test: pd.DataFrame
) -> float:
    if NBA_BASELINE_METRICS_JSON.exists():
        try:
            saved = json.loads(NBA_BASELINE_METRICS_JSON.read_text(encoding="utf-8"))
            v2 = saved.get("v2_comparison", {}).get("holdout", {})
            if v2.get("log_loss") is not None:
                return float(v2["log_loss"])
            m = saved.get("metrics", {}).get("logistic_regression_v2_score", {})
            if m.get("log_loss") is not None:
                return float(m["log_loss"])
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass
    _, metrics = train_logistic(
        train,
        test,
        FEATURE_COLUMNS_WAVE2,
        "logistic_regression_v2_score",
    )
    return metrics.log_loss


def run_training() -> dict[str, Any]:
    raw = load_games()
    feat = build_features_for_history(raw)
    feat = feat[feat["home_score"].notna() & feat["away_score"].notna()].copy()
    feat["margin"] = feat["home_score"].astype(float) - feat["away_score"].astype(float)

    train, test = time_split(feat)

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

    v2_logistic_ll = _v2_logistic_holdout_log_loss(train, test)
    beats_v2 = margin_derived_ml_ll < v2_logistic_ll

    y_test = test["home_win"].values
    home_rate = float(train["home_win"].mean())
    from app.models.nba_baseline import (
        compute_metrics,
        constant_market_proxy_log_loss,
        predict_elo,
    )

    full_for_elo = feat.copy()
    n_train = len(train)
    elo_probs = predict_elo(full_for_elo)[n_train:]
    elo_metrics = compute_metrics("elo_baseline", y_test, elo_probs)
    market_proxy_ll = constant_market_proxy_log_loss(y_test, home_rate)
    home_rate_probs = np.full(len(y_test), np.clip(home_rate, 1e-6, 1 - 1e-6))
    home_rate_ll = _binary_log_loss(y_test, home_rate_probs)
    naive_ll = min(home_rate_ll, elo_metrics.log_loss)
    ml_gate_for_promotion = production_gate_passes(
        margin_derived_ml_ll, naive_ll, market_proxy_ll
    )

    artifact = {
        "model": reg,
        "model_version": "v1_margin_gbr_normal",
        "feature_columns": FEATURE_COLUMNS,
        "feature_set": "v2_score_rolling",
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
            "margin_derived_ml_log_loss": margin_derived_ml_ll,
            "v2_logistic_log_loss": v2_logistic_ll,
        }
    )

    manifest = {
        "track": "nba_spread",
        "model_version": artifact["model_version"],
        "path": "data/processed/nba_margin_model.joblib",
        "feature_set": artifact["feature_set"],
        "production_ready": gate_passes,
        "promoted_at": _iso_now(),
    }
    ACTIVE_MARGIN_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    ACTIVE_MARGIN_MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    results: dict[str, Any] = {
        "train_seasons": list(TRAIN_SEASONS),
        "holdout_season": HOLDOUT_SEASON,
        "train_rows": len(train),
        "holdout_rows": len(test),
        "feature_set": "v2_score_rolling",
        "holdout_mae_margin": round(mae, 4),
        "holdout_margin_std": round(margin_std, 4),
        "proxy_cover_log_loss_home": round(proxy_home_ll, 4),
        "proxy_cover_log_loss_away": round(proxy_away_ll, 4),
        "margin_derived_ml_log_loss": round(margin_derived_ml_ll, 4),
        "v2_logistic_log_loss": round(v2_logistic_ll, 4),
        "beats_v2_logistic": beats_v2,
        "margin_production_gate_passes": gate_passes,
        "board_spread_enabled": gate_passes,
        "moneyline_promotion_eligible": beats_v2 and ml_gate_for_promotion,
        "proxy_lines": artifact["proxy_lines"],
        "note": (
            "No free historical NBA spread CSV; holdout eval uses ±5.5 proxy. "
            "Live board uses actual book lines from Odds API."
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
        f"No NBA margin model at {MODEL_ARTIFACT}. Run scripts/train_nba_margin.py first."
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
    if "home_last10_pts_for" in df.columns and "elo_home_pre" in df.columns:
        return df.copy()
    artifact = load_margin_artifact()
    rest_fill = float(artifact.get("rest_fill", DEFAULT_REST_FILL))
    pts_fill = float(artifact.get("pts_fill", DEFAULT_PTS_FILL))
    return build_features_for_slate(df, rest_fill=rest_fill, pts_fill=pts_fill)


def predict_margin(df: pd.DataFrame) -> np.ndarray:
    artifact = load_margin_artifact()
    cols = artifact["feature_columns"]
    prepared = _prepare_features(df)
    return artifact["model"].predict(prepared[cols].values)


def predict_home_win_proba_from_margin(df: pd.DataFrame) -> np.ndarray:
    artifact = load_margin_artifact()
    std = float(artifact["margin_std"])
    margins = predict_margin(df)
    probs = np.array([1.0 - norm_cdf(0.0, float(m), std) for m in margins])
    return np.clip(probs, 1e-6, 1 - 1e-6)


def predict_spread_covers(df: pd.DataFrame) -> pd.DataFrame:
    """Add predicted margin and cover probs at each row's book spread (if present)."""
    artifact = load_margin_artifact()
    cols = artifact["feature_columns"]
    std = float(artifact["margin_std"])
    prepared = _prepare_features(df)
    missing = [c for c in cols if c not in prepared.columns]
    if missing:
        raise ValueError(f"Spread model missing feature columns: {missing}")

    out = prepared.copy()
    for col in df.columns:
        if col not in out.columns:
            out[col] = df[col].values
    margins = artifact["model"].predict(out[cols].values)
    out["model_margin"] = margins
    home_probs: list[float | None] = []
    away_probs: list[float | None] = []
    for row, margin in zip(out.itertuples(index=False), margins):
        hp = getattr(row, "home_spread_point", None)
        ap = getattr(row, "away_spread_point", None)
        if hp is not None and not (isinstance(hp, float) and math.isnan(hp)):
            home_probs.append(
                round(model_prob_home_cover(float(margin), std, float(hp)), 4)
            )
        else:
            home_probs.append(None)
        if ap is not None and not (isinstance(ap, float) and math.isnan(ap)):
            away_probs.append(
                round(model_prob_away_cover(float(margin), std, float(ap)), 4)
            )
        else:
            away_probs.append(None)
    out["model_prob_home_cover"] = home_probs
    out["model_prob_away_cover"] = away_probs
    return out


def format_metrics_table(results: dict[str, Any]) -> str:
    lines = [
        "| Metric | Value |",
        "|--------|-------|",
        (f"| Holdout MAE (margin) | {results['holdout_mae_margin']:.4f} |"),
        (f"| Holdout margin std | {results['holdout_margin_std']:.4f} |"),
        (
            f"| Proxy cover log loss (home @ {PROXY_HOME_SPREAD}) | "
            f"{results['proxy_cover_log_loss_home']:.4f} |"
        ),
        (
            f"| Proxy cover log loss (away @ {PROXY_AWAY_SPREAD}) | "
            f"{results['proxy_cover_log_loss_away']:.4f} |"
        ),
        (
            f"| Margin-derived ML log loss | "
            f"{results['margin_derived_ml_log_loss']:.4f} |"
        ),
        (
            f"| v2 logistic log loss | "
            f"{results['v2_logistic_log_loss']:.4f} |"
        ),
        (f"| Beats v2 logistic | {results['beats_v2_logistic']} |"),
        (
            f"| Margin production gate | "
            f"{results['margin_production_gate_passes']} |"
        ),
    ]
    return "\n".join(lines)
