"""MLB moneyline ensemble: logistic + GBC + Elo with time-based calibration."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

from app.config import PROJECT_ROOT
from app.models.mlb_baseline import (
    ELO_HOME_ADV,
    ELO_START,
    FEATURE_COLUMNS,
    HoldoutMetrics,
    _elo_expected,
    _season_era_medians,
    attach_elo_features,
    compute_metrics,
    load_games,
    train_logistic,
)
from app.models.platt_calibration import PlattCalibrator
from app.models.production_pipeline import (
    build_moneyline_platt_artifact,
    save_moneyline_promotion,
)

ENSEMBLE_METRICS_JSON = PROJECT_ROOT / "data" / "processed" / "mlb_ensemble_metrics.json"
ENSEMBLE_ARTIFACT_PATH = PROJECT_ROOT / "data" / "processed" / "mlb_ensemble_model.joblib"
ENSEMBLE_CALIB_SEASON = 2024
ENSEMBLE_HOLDOUT_SEASON = 2025

ENSEMBLE_WEIGHT_LOGISTIC = 0.35
ENSEMBLE_WEIGHT_GBC = 0.45
ENSEMBLE_WEIGHT_ELO = 0.20

CONFIDENCE_NO_PICK = 0.54
CONFIDENCE_LOW = 0.58
CONFIDENCE_MODERATE = 0.62
CONFIDENCE_HIGH = 0.67

DEFAULT_MIN_EDGE = 0.08

# Placeholder columns for future data sources (HistGBM handles NaN at inference).
OPTIONAL_MARKET_COLUMNS = [
    "market_prob_home",
    "market_prob_move",
    "home_lineup_strength",
    "away_lineup_strength",
    "home_offense_vs_sp_handedness",
    "away_offense_vs_sp_handedness",
]

GBC_EXTRA_COLUMNS = [
    "home_pitcher_era_l3",
    "away_pitcher_era_l3",
    "home_pitcher_whip_l3",
    "away_pitcher_whip_l3",
    "home_pitcher_ip_l3",
    "away_pitcher_ip_l3",
    "home_pitcher_era_l5",
    "away_pitcher_era_l5",
    "home_pitcher_whip_l5",
    "away_pitcher_whip_l5",
    "home_bullpen_era_14d",
    "away_bullpen_era_14d",
    "home_bullpen_ip_3d",
    "away_bullpen_ip_3d",
    "home_team_elo",
    "away_team_elo",
    "elo_diff",
] + OPTIONAL_MARKET_COLUMNS


@dataclass
class ModelPickResult:
    model_pick_side: str | None
    model_pick_team: str | None
    model_pick_prob: float | None
    model_pick_action: str
    model_confidence: str
    model_confidence_prob: float | None


def attach_elo_strength_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Map chronological Elo ratings to home_team_elo / away_team_elo / elo_diff."""
    out = df.copy()
    if "elo_home_pre" not in out.columns or "elo_away_pre" not in out.columns:
        out["home_team_elo"] = ELO_START
        out["away_team_elo"] = ELO_START
    else:
        out["home_team_elo"] = out["elo_home_pre"]
        out["away_team_elo"] = out["elo_away_pre"]
    out["elo_diff"] = out["home_team_elo"] - out["away_team_elo"]
    return out


def elo_prob_home(home_elo: float, away_elo: float) -> float:
    return float(_elo_expected(float(home_elo), float(away_elo)))


def elo_prob_home_series(df: pd.DataFrame) -> np.ndarray:
    return np.array(
        [
            elo_prob_home(h, a)
            for h, a in zip(df["home_team_elo"], df["away_team_elo"], strict=False)
        ],
        dtype=float,
    )


def confidence_tier(side_prob: float | None) -> str:
    if side_prob is None or math.isnan(side_prob):
        return "—"
    p = float(side_prob)
    if p < CONFIDENCE_NO_PICK:
        return "Lean only"
    if p < CONFIDENCE_LOW:
        return "Low"
    if p < CONFIDENCE_MODERATE:
        return "Moderate"
    if p < CONFIDENCE_HIGH:
        return "High"
    return "Very high"


def model_pick_from_prob(
    prob_home: float,
    home_team: str,
    away_team: str,
    *,
    block_strong_picks: bool = False,
) -> ModelPickResult:
    """Map ensemble P(home) to pick, action, and confidence bucket."""
    side_prob_home = float(prob_home)
    side_prob_away = 1.0 - side_prob_home
    if side_prob_home >= side_prob_away:
        side = "home"
        team = home_team
        pick_prob = side_prob_home
    else:
        side = "away"
        team = away_team
        pick_prob = side_prob_away

    tier = confidence_tier(pick_prob)
    if pick_prob < CONFIDENCE_NO_PICK or block_strong_picks:
        return ModelPickResult(
            model_pick_side=side,
            model_pick_team=team,
            model_pick_prob=round(pick_prob, 4),
            model_pick_action="lean_only",
            model_confidence="Lean only" if not block_strong_picks else "Blocked (stale data)",
            model_confidence_prob=round(pick_prob, 4),
        )

    return ModelPickResult(
        model_pick_side=side,
        model_pick_team=team,
        model_pick_prob=round(pick_prob, 4),
        model_pick_action="pick",
        model_confidence=tier,
        model_confidence_prob=round(pick_prob, 4),
    )


def _attach_market_probs(feat: pd.DataFrame, raw: pd.DataFrame) -> pd.DataFrame:
    from app.odds.mlb_odds_free import ODDS_2025_CSV
    from app.odds.odds_math import market_probs_from_american
    from app.odds.team_aliases import is_valid_american_odds, normalize_team_name

    out = feat.copy()
    out["market_prob_home"] = np.nan
    out["market_prob_move"] = np.nan
    if not ODDS_2025_CSV.exists():
        return out

    odds = pd.read_csv(ODDS_2025_CSV)
    odds["date"] = pd.to_datetime(odds["date"]).dt.strftime("%Y-%m-%d")
    holdout = raw.copy()
    holdout["date"] = holdout["date"].dt.strftime("%Y-%m-%d")
    holdout["home_team"] = holdout["home_team"].map(normalize_team_name)
    holdout["away_team"] = holdout["away_team"].map(normalize_team_name)
    keys = holdout[["game_id", "date", "home_team", "away_team"]].copy()
    keys = keys.merge(odds, on=["date", "home_team", "away_team"], how="left")
    market: list[float | None] = []
    for row in keys.itertuples(index=False):
        if pd.isna(row.home_ml) or pd.isna(row.away_ml):
            market.append(None)
            continue
        if not is_valid_american_odds(row.home_ml) or not is_valid_american_odds(
            row.away_ml
        ):
            market.append(None)
            continue
        mh, _ = market_probs_from_american(int(row.home_ml), int(row.away_ml))
        market.append(mh)
    out["market_prob_home"] = market
    return out


def _ensure_optional_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in OPTIONAL_MARKET_COLUMNS:
        if col not in out.columns:
            out[col] = np.nan
    return out


def _usable_gbc_columns(train_df: pd.DataFrame, cols: list[str]) -> list[str]:
    """Drop constant or all-null columns that break HistGradientBoosting binning."""
    frame = _ensure_optional_columns(train_df)
    usable: list[str] = []
    for col in cols:
        if col not in frame.columns:
            continue
        if frame[col].nunique(dropna=True) >= 2:
            usable.append(col)
    if not usable:
        raise ValueError("No usable GBC feature columns after filtering constants")
    return usable


def gbc_feature_columns(logistic_cols: list[str]) -> list[str]:
    cols = list(dict.fromkeys([*logistic_cols, *GBC_EXTRA_COLUMNS]))
    return cols


def _blend_probs(
    logistic_p: np.ndarray,
    gbc_p: np.ndarray,
    elo_p: np.ndarray,
    *,
    w_log: float = ENSEMBLE_WEIGHT_LOGISTIC,
    w_gbc: float = ENSEMBLE_WEIGHT_GBC,
    w_elo: float = ENSEMBLE_WEIGHT_ELO,
) -> np.ndarray:
    return np.clip(w_log * logistic_p + w_gbc * gbc_p + w_elo * elo_p, 1e-6, 1 - 1e-6)


def _bucket_name(side_prob: float) -> str:
    if side_prob < CONFIDENCE_NO_PICK:
        return "no_pick"
    if side_prob < CONFIDENCE_LOW:
        return "low"
    if side_prob < CONFIDENCE_MODERATE:
        return "moderate"
    if side_prob < CONFIDENCE_HIGH:
        return "high"
    return "very_high"


def accuracy_by_confidence_bucket(
    y_true: np.ndarray,
    prob_home: np.ndarray,
) -> dict[str, dict[str, float | int]]:
    buckets: dict[str, dict[str, float | int]] = {}
    for name in ("no_pick", "low", "moderate", "high", "very_high"):
        buckets[name] = {"n": 0, "accuracy": None}

    for y, ph in zip(y_true, prob_home, strict=False):
        side_prob = float(ph) if float(ph) >= 0.5 else 1.0 - float(ph)
        bucket = _bucket_name(side_prob)
        pick_home = float(ph) >= 0.5
        correct = int(pick_home) == int(y)
        buckets[bucket]["n"] = int(buckets[bucket]["n"]) + 1
        prev = buckets[bucket].get("_correct", 0)
        buckets[bucket]["_correct"] = int(prev) + int(correct)

    total = len(y_true)
    no_pick_n = int(buckets["no_pick"]["n"])
    for name, data in buckets.items():
        n = int(data["n"])
        if n > 0:
            data["accuracy"] = round(int(data.pop("_correct", 0)) / n, 4)
        else:
            data.pop("_correct", None)
    buckets["summary"] = {
        "no_pick_pct": round(no_pick_n / total, 4) if total else 0.0,
        "total_games": total,
    }
    return buckets


def _plus_ev_roi(
    prob_home: np.ndarray,
    y_true: np.ndarray,
    market_home: np.ndarray,
    min_edge: float = DEFAULT_MIN_EDGE,
) -> dict[str, float | None]:
    """Flat-stake ROI on +EV sides vs market (holdout rows with market only)."""
    stakes = 0
    profit = 0.0
    picks = 0
    for ph, y, mh in zip(prob_home, y_true, market_home, strict=False):
        if mh is None or (isinstance(mh, float) and math.isnan(mh)):
            continue
        mh_f = float(mh)
        ma_f = 1.0 - mh_f
        edge_home = float(ph) - mh_f
        edge_away = (1.0 - float(ph)) - ma_f
        if edge_home >= edge_away:
            if edge_home < min_edge:
                continue
            side_home = True
            edge = edge_home
        else:
            if edge_away < min_edge:
                continue
            side_home = False
            edge = edge_away
        won = bool(y) if side_home else not bool(y)
        picks += 1
        stakes += 1
        if won:
            profit += edge / max(mh_f if side_home else ma_f, 1e-6)
        else:
            profit -= 1.0
    if picks == 0:
        return {"plus_ev_picks": 0, "plus_ev_roi": None}
    return {
        "plus_ev_picks": picks,
        "plus_ev_roi": round(profit / stakes, 4),
    }


def _mean_clv_placeholder() -> float | None:
    """Use forward CLV log summary when available."""
    from app.services.forward_clv import FORWARD_CLV_LOG

    if not FORWARD_CLV_LOG.exists():
        return None
    rows: list[dict[str, Any]] = []
    for line in FORWARD_CLV_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    clvs = [
        r["clv_implied_prob"]
        for r in rows
        if r.get("clv_implied_prob") is not None and r.get("settled")
    ]
    if not clvs:
        return None
    return round(float(np.mean(clvs)), 4)


def promotion_gate_improves(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    min_improvements: int = 2,
) -> tuple[bool, list[str]]:
    """Promote only if candidate improves at least two tracked metrics."""
    checks = [
        ("log_loss", "lower"),
        ("brier", "lower"),
        ("high_confidence_accuracy", "higher"),
        ("plus_ev_roi", "higher"),
        ("mean_clv", "higher"),
    ]
    improved: list[str] = []
    for key, direction in checks:
        old = baseline.get(key)
        new = candidate.get(key)
        if old is None or new is None:
            continue
        if direction == "lower" and new < old:
            improved.append(key)
        elif direction == "higher" and new > old:
            improved.append(key)
    return len(improved) >= min_improvements, improved


def evaluate_ensemble_holdout(
    y_true: np.ndarray,
    prob_home: np.ndarray,
    market_home: np.ndarray | None = None,
) -> dict[str, Any]:
    prob_home = np.clip(prob_home, 1e-6, 1 - 1e-6)
    metrics = compute_metrics("ensemble_holdout", y_true, prob_home)
    buckets = accuracy_by_confidence_bucket(y_true, prob_home)
    high_acc = buckets.get("high", {}).get("accuracy")
    very_acc = buckets.get("very_high", {}).get("accuracy")
    high_n = int(buckets.get("high", {}).get("n", 0))
    very_n = int(buckets.get("very_high", {}).get("n", 0))
    if high_n + very_n > 0 and high_acc is not None and very_acc is not None:
        high_conf_accuracy = round(
            (high_acc * high_n + very_acc * very_n) / (high_n + very_n), 4
        )
    elif high_n > 0:
        high_conf_accuracy = high_acc
    elif very_n > 0:
        high_conf_accuracy = very_acc
    else:
        high_conf_accuracy = None

    roi = {"plus_ev_picks": 0, "plus_ev_roi": None}
    if market_home is not None:
        roi = _plus_ev_roi(prob_home, y_true, market_home)

    return {
        "winner_accuracy": round(metrics.accuracy, 4),
        "log_loss": round(metrics.log_loss, 6),
        "brier": round(metrics.brier, 6),
        "accuracy_by_confidence": buckets,
        "high_confidence_accuracy": high_conf_accuracy,
        "plus_ev_roi": roi.get("plus_ev_roi"),
        "plus_ev_picks": roi.get("plus_ev_picks"),
        "mean_clv": _mean_clv_placeholder(),
        "no_pick_pct": buckets.get("summary", {}).get("no_pick_pct"),
    }


def _predict_logistic_component(
    prepared: pd.DataFrame,
    logistic_payload: dict[str, Any],
) -> np.ndarray:
    cols = logistic_payload["feature_columns"]
    raw = logistic_payload["model"].predict_proba(prepared[cols].values)[:, 1]
    platt = logistic_payload.get("platt_calibrator")
    if platt is not None:
        return platt.transform(raw)
    return raw


def _predict_gbc_component(
    prepared: pd.DataFrame,
    artifact: dict[str, Any],
) -> np.ndarray:
    gbc = artifact["gbc_model"]
    cols = artifact["gbc_feature_columns"]
    frame = _ensure_optional_columns(prepared)
    x = frame.reindex(columns=cols, fill_value=np.nan)
    return gbc.predict_proba(x.values)[:, 1]


def predict_ensemble_components(
    prepared: pd.DataFrame,
    artifact: dict[str, Any],
) -> dict[str, np.ndarray]:
    prepared = attach_elo_strength_columns(prepared)
    logistic_p = _predict_logistic_component(prepared, artifact["logistic"])
    gbc_p = _predict_gbc_component(prepared, artifact)
    elo_p = elo_prob_home_series(prepared)
    raw_blend = _blend_probs(logistic_p, gbc_p, elo_p)
    ens_platt = artifact.get("ensemble_platt")
    if ens_platt is not None:
        final = ens_platt.transform(raw_blend)
    else:
        final = raw_blend
    return {
        "logistic_prob_home": logistic_p,
        "gbc_prob_home": gbc_p,
        "elo_prob_home": elo_p,
        "ensemble_raw_prob_home": raw_blend,
        "ensemble_prob_home": final,
    }


def predict_ensemble_home_proba(
    df: pd.DataFrame,
    artifact: dict[str, Any],
) -> np.ndarray:
    comps = predict_ensemble_components(df, artifact)
    return comps["ensemble_prob_home"]


def run_ensemble_training(
    raw: pd.DataFrame | None = None,
    *,
    promote: bool = True,
) -> dict[str, Any]:
    """
    Time-based training:
    - Logistic baseline: 2023 train + Platt (via production_pipeline)
    - GBC: fit 2023–2024
    - Ensemble Platt: fit on 2024 blended preds (no holdout leakage)
    - Holdout metrics: 2025
    """
    from app.features.feature_selection import drop_redundant_features
    from app.features.mlb_pregame import FEATURE_COLUMNS_WAVE1, build_features_for_history

    games = raw if raw is not None else load_games()
    feat_all = build_features_for_history(games)
    feat_all = attach_elo_features(feat_all)
    feat_all = attach_elo_strength_columns(feat_all)
    feat_all = _attach_market_probs(feat_all, games)

    train_mask = feat_all["season"].isin([2023, 2024])
    cal_mask = feat_all["season"] == ENSEMBLE_CALIB_SEASON
    holdout_mask = feat_all["season"] == ENSEMBLE_HOLDOUT_SEASON
    train_w = feat_all[train_mask].copy()
    cal_w = feat_all[cal_mask].copy()
    holdout_w = feat_all[holdout_mask].copy()
    if train_w.empty or cal_w.empty or holdout_w.empty:
        raise ValueError(
            f"Need 2023–2024 train, {ENSEMBLE_CALIB_SEASON} cal, "
            f"and {ENSEMBLE_HOLDOUT_SEASON} holdout for ensemble training"
        )

    pruned_cols, dropped_cols, _ = drop_redundant_features(train_w, FEATURE_COLUMNS_WAVE1)
    logistic_payload = build_moneyline_platt_artifact(
        pruned_cols,
        raw=games,
        wave1_pruned_columns=pruned_cols,
        wave1_dropped_columns=dropped_cols,
    )

    gbc_cols = gbc_feature_columns(pruned_cols)
    gbc_cols = _usable_gbc_columns(train_w, gbc_cols)
    gbc = HistGradientBoostingClassifier(
        max_depth=5,
        learning_rate=0.06,
        max_iter=200,
        random_state=42,
    )
    gbc.fit(
        _ensure_optional_columns(train_w)[gbc_cols].values,
        train_w["home_win"].values,
    )

    prepared_cal = _ensure_optional_columns(cal_w)
    logistic_cal = _predict_logistic_component(prepared_cal, logistic_payload)
    gbc_cal = gbc.predict_proba(prepared_cal[gbc_cols].values)[:, 1]
    elo_cal = elo_prob_home_series(prepared_cal)
    raw_blend_cal = _blend_probs(logistic_cal, gbc_cal, elo_cal)

    ens_platt = PlattCalibrator()
    ens_platt.fit(raw_blend_cal, cal_w["home_win"].values)

    prepared_holdout = _ensure_optional_columns(holdout_w)
    logistic_h = _predict_logistic_component(prepared_holdout, logistic_payload)
    gbc_h = gbc.predict_proba(prepared_holdout[gbc_cols].values)[:, 1]
    elo_h = elo_prob_home_series(prepared_holdout)
    raw_blend_h = _blend_probs(logistic_h, gbc_h, elo_h)
    final_h = ens_platt.transform(raw_blend_h)

    market_h = holdout_w["market_prob_home"].tolist()
    holdout_metrics = evaluate_ensemble_holdout(
        holdout_w["home_win"].values,
        final_h,
        market_h,
    )

    logistic_holdout = _predict_logistic_component(prepared_holdout, logistic_payload)
    baseline_metrics = evaluate_ensemble_holdout(
        holdout_w["home_win"].values,
        logistic_holdout,
        market_h,
    )

    should_promote, improved = promotion_gate_improves(
        baseline_metrics, holdout_metrics
    )

    train_games = games[games["season"].isin([2023, 2024, 2025])]
    era_medians = _season_era_medians(train_games)
    rest_fill = float(
        pd.concat([train_games["home_rest_days"], train_games["away_rest_days"]])
        .dropna()
        .median()
    )
    if math.isnan(rest_fill):
        rest_fill = 1.0

    artifact = {
        "model_version": "v4_ensemble_logistic_gbc_elo",
        "ensemble_version": True,
        "ensemble_weights": {
            "logistic": ENSEMBLE_WEIGHT_LOGISTIC,
            "gbc": ENSEMBLE_WEIGHT_GBC,
            "elo": ENSEMBLE_WEIGHT_ELO,
        },
        "logistic": logistic_payload,
        "gbc_model": gbc,
        "gbc_feature_columns": gbc_cols,
        "ensemble_platt": ens_platt,
        "feature_columns": pruned_cols,
        "era_medians": era_medians,
        "rest_fill": rest_fill,
        "confidence_thresholds": {
            "no_pick": CONFIDENCE_NO_PICK,
            "low": CONFIDENCE_LOW,
            "moderate": CONFIDENCE_MODERATE,
            "high": CONFIDENCE_HIGH,
        },
        "wave1_pruned_columns": pruned_cols,
        "wave1_dropped_columns": dropped_cols,
    }

    results = {
        "train_seasons": [2023, 2024],
        "calibration_season": ENSEMBLE_CALIB_SEASON,
        "holdout_season": ENSEMBLE_HOLDOUT_SEASON,
        "holdout": holdout_metrics,
        "baseline_logistic_holdout": baseline_metrics,
        "promotion_improved_metrics": improved,
        "promoted": False,
    }

    ENSEMBLE_ARTIFACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, ENSEMBLE_ARTIFACT_PATH)

    if promote and should_promote:
        from app.models.mlb_baseline import MODEL_ARTIFACT

        joblib.dump(artifact, MODEL_ARTIFACT)
        save_moneyline_promotion(
            "train_mlb_ensemble",
            artifact,
            feature_set="mlb_ensemble_v4",
        )
        results["promoted"] = True

    ENSEMBLE_METRICS_JSON.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results


def load_ensemble_artifact() -> dict[str, Any] | None:
    from app.models.mlb_baseline import load_model_artifact

    artifact = load_model_artifact()
    if artifact.get("ensemble_version"):
        return artifact
    if ENSEMBLE_ARTIFACT_PATH.exists():
        loaded = joblib.load(ENSEMBLE_ARTIFACT_PATH)
        if loaded.get("ensemble_version"):
            return loaded
    return None


def is_ensemble_artifact(artifact: dict[str, Any]) -> bool:
    return bool(artifact.get("ensemble_version"))
