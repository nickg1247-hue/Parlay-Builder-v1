"""NFL home_win baseline: logistic + Platt calibration + Elo comparison."""

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
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app.config import PROJECT_ROOT
from app.db.database import get_connection
from app.features.nfl_pregame import FEATURE_COLUMNS, FEATURE_COLUMNS_V2, build_features_for_history
from app.ingest.nfl import DEFAULT_REST_FILL
from app.models.platt_calibration import PlattCalibrator

MODEL_ARTIFACT = PROJECT_ROOT / "data" / "processed" / "nfl_baseline_model.joblib"
METRICS_JSON = PROJECT_ROOT / "data" / "processed" / "nfl_baseline_metrics.json"
MODELS_DIR = PROJECT_ROOT / "data" / "processed" / "models"
ACTIVE_NFL_MANIFEST = PROJECT_ROOT / "data" / "processed" / "active_nfl_model.json"
PARQUET_PATH = PROJECT_ROOT / "data" / "processed" / "nfl_games.parquet"

BASE_TRAIN_SEASONS = (2019, 2020, 2021, 2022, 2023)
PLATT_SEASON = 2024
HOLDOUT_SEASON = 2025
REGRESSION_TRAIN_SEASONS = (2019, 2020, 2021, 2022, 2023, 2024)
FEATURE_SET = "nfl_v2"
MODEL_VERSION = "v2_gbr_elo_tossup"
ACCURACY_HARD_MIN = 0.60
TOSSUP_CUT_GRID = (0.03, 0.05, 0.07, 0.10, 0.12, 0.15)

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
            df = pd.read_sql("SELECT * FROM nfl_games ORDER BY date, game_id", conn)
        finally:
            conn.close()
    if df.empty:
        raise FileNotFoundError(
            f"No NFL games at {PARQUET_PATH}. Run scripts/bootstrap_nfl.py first."
        )
    for col, default in (
        ("week", 0),
        ("divisional", 0),
        ("neutral_site", 0),
        ("home_team_abbr", ""),
        ("away_team_abbr", ""),
        ("home_team_id", ""),
        ("away_team_id", ""),
    ):
        if col not in df.columns:
            df[col] = default
        else:
            df[col] = df[col].fillna(default)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values(["date", "game_id"]).reset_index(drop=True)


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


def time_split_regression(
    df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    train = df[df["season"].isin(REGRESSION_TRAIN_SEASONS)].copy()
    holdout = df[df["season"] == HOLDOUT_SEASON].copy()
    if train.empty or holdout.empty:
        raise ValueError(
            f"Expected regression train {REGRESSION_TRAIN_SEASONS}, "
            f"holdout {HOLDOUT_SEASON}"
        )
    return train, holdout


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


def _row_neutral(row) -> bool:
    if hasattr(row, "home_field") and pd.notna(getattr(row, "home_field", None)):
        return int(row.home_field) == 0
    if hasattr(row, "neutral_site") and pd.notna(getattr(row, "neutral_site", None)):
        return int(row.neutral_site) == 1
    return False


def _elo_team_key(row, side: str) -> str:
    from app.ingest.nfl import normalize_abbr

    abbr = getattr(row, f"{side}_team_abbr", None)
    if abbr is not None and not (isinstance(abbr, float) and pd.isna(abbr)):
        key = normalize_abbr(str(abbr))
        if key:
            return key
    name = getattr(row, f"{side}_team", "")
    return normalize_abbr(str(name)) or str(name)


def attach_elo_features(df: pd.DataFrame, *, update_ratings: bool = True) -> pd.DataFrame:
    out = df.copy()
    ratings: dict[str, float] = {}
    home_elos: list[float] = []
    away_elos: list[float] = []
    for row in out.itertuples(index=False):
        home_key = _elo_team_key(row, "home")
        away_key = _elo_team_key(row, "away")
        home_elos.append(ratings.get(home_key, ELO_START))
        away_elos.append(ratings.get(away_key, ELO_START))
        hw = getattr(row, "home_win", None)
        if update_ratings and hw is not None and pd.notna(hw):
            home, away = _elo_update(
                ratings.get(home_key, ELO_START),
                ratings.get(away_key, ELO_START),
                int(hw),
                neutral=_row_neutral(row),
            )
            ratings[home_key] = home
            ratings[away_key] = away
    out["elo_home_pre"] = home_elos
    out["elo_away_pre"] = away_elos
    return out


def current_elo_ratings(history: pd.DataFrame) -> dict[str, float]:
    ratings: dict[str, float] = {}
    hist = history.sort_values(["date", "game_id"]).reset_index(drop=True)
    for row in hist.itertuples(index=False):
        home_key = _elo_team_key(row, "home")
        away_key = _elo_team_key(row, "away")
        home, away = _elo_update(
            ratings.get(home_key, ELO_START),
            ratings.get(away_key, ELO_START),
            int(row.home_win),
            neutral=_row_neutral(row),
        )
        ratings[home_key] = home
        ratings[away_key] = away
    return ratings


def attach_elo_for_slate(
    df: pd.DataFrame,
    history: pd.DataFrame | None = None,
) -> pd.DataFrame:
    hist = history if history is not None else load_games()
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"])
    hist = hist.copy()
    hist["date"] = pd.to_datetime(hist["date"])
    if "home_win" in hist.columns:
        hist = hist[hist["home_win"].notna()]
    # Use games before this slate's kickoffs — not before the earliest row
    # in `df`. A history+slate frame starts in 2019 and would wipe ratings.
    min_date = out["date"].min()
    prior = hist[hist["date"] < min_date]
    ratings = current_elo_ratings(prior) if not prior.empty else {}
    home_keys = [_elo_team_key(row, "home") for row in out.itertuples(index=False)]
    away_keys = [_elo_team_key(row, "away") for row in out.itertuples(index=False)]
    out["elo_home_pre"] = [ratings.get(t, ELO_START) for t in home_keys]
    out["elo_away_pre"] = [ratings.get(t, ELO_START) for t in away_keys]
    return out


def predict_elo(df: pd.DataFrame) -> np.ndarray:
    ratings: dict[str, float] = {}
    probs: list[float] = []
    for row in df.itertuples(index=False):
        home_key = _elo_team_key(row, "home")
        away_key = _elo_team_key(row, "away")
        home = ratings.get(home_key, ELO_START)
        away = ratings.get(away_key, ELO_START)
        neutral = _row_neutral(row)
        probs.append(_elo_expected(home, away, neutral=neutral))
        home, away = _elo_update(home, away, int(row.home_win), neutral=neutral)
        ratings[home_key] = home
        ratings[away_key] = away
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


def train_gbr(
    train: pd.DataFrame,
    feature_cols: list[str] | None = None,
) -> GradientBoostingClassifier:
    cols = feature_cols or FEATURE_COLUMNS_V2
    model = GradientBoostingClassifier(
        n_estimators=120,
        max_depth=2,
        learning_rate=0.05,
        subsample=0.85,
        random_state=42,
    )
    model.fit(train[cols].values, train["home_win"].values)
    return model


def elo_probs_from_pre(df: pd.DataFrame) -> np.ndarray:
    probs: list[float] = []
    for row in df.itertuples(index=False):
        home = float(getattr(row, "elo_home_pre", ELO_START) or ELO_START)
        away = float(getattr(row, "elo_away_pre", ELO_START) or ELO_START)
        home_field = getattr(row, "home_field", 1)
        neutral = int(home_field or 0) == 0
        probs.append(_elo_expected(home, away, neutral=neutral))
    return np.array(probs)


def apply_elo_tossup(model_p: np.ndarray, elo_p: np.ndarray, cut: float) -> np.ndarray:
    if cut is None or cut <= 0:
        return np.asarray(model_p, dtype=float)
    model_p = np.asarray(model_p, dtype=float)
    elo_p = np.asarray(elo_p, dtype=float)
    return np.where(np.abs(model_p - 0.5) < float(cut), elo_p, model_p)


def tune_tossup_cut(y_true: np.ndarray, model_p: np.ndarray, elo_p: np.ndarray) -> float:
    best_t = 0.0
    best = float(accuracy_score(y_true, np.asarray(model_p) >= 0.5))
    for t in TOSSUP_CUT_GRID:
        mixed = apply_elo_tossup(model_p, elo_p, t)
        acc = float(accuracy_score(y_true, mixed >= 0.5))
        if acc > best + 1e-9:
            best = acc
            best_t = float(t)
    return best_t


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


def _manifest_path(path: Path) -> str:
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def save_nfl_promotion(run_id: str, artifact: dict[str, Any]) -> dict[str, Any]:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    versioned = MODELS_DIR / f"{run_id}.joblib"
    joblib.dump(artifact, versioned)
    joblib.dump(artifact, MODEL_ARTIFACT)
    manifest = {
        "track": "nfl_moneyline",
        "run_id": run_id,
        "path": _manifest_path(MODEL_ARTIFACT),
        "feature_set": artifact["feature_set"],
        "model_version": artifact["model_version"],
        "promoted_at": _iso_now(),
    }
    ACTIVE_NFL_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    ACTIVE_NFL_MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def load_model_artifact() -> dict[str, Any]:
    if ACTIVE_NFL_MANIFEST.exists():
        manifest = json.loads(ACTIVE_NFL_MANIFEST.read_text(encoding="utf-8"))
        path = Path(manifest["path"])
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if path.exists():
            return joblib.load(path)
    if MODEL_ARTIFACT.exists():
        return joblib.load(MODEL_ARTIFACT)
    raise FileNotFoundError(
        f"No NFL model at {MODEL_ARTIFACT}. Run scripts/bootstrap_nfl.py first."
    )


def predict_home_win_proba(df: pd.DataFrame) -> np.ndarray:
    from app.features.nfl_pregame import build_features_for_slate

    artifact = load_model_artifact()
    cols = list(artifact.get("feature_columns") or FEATURE_COLUMNS_V2)
    rest_fill = float(artifact.get("rest_fill", DEFAULT_REST_FILL))
    prepared = build_features_for_slate(df, rest_fill=rest_fill)
    raw = artifact["model"].predict_proba(prepared[cols].values)[:, 1]
    platt = artifact.get("platt_calibrator")
    if platt is not None:
        raw = platt.transform(raw)
    cut = float(artifact.get("tossup_cut") or 0.0)
    if cut > 0:
        raw = apply_elo_tossup(raw, elo_probs_from_pre(prepared), cut)
    by_id = dict(zip(prepared["game_id"].astype(str), raw))
    mapped = df["game_id"].astype(str).map(by_id)
    return mapped.to_numpy(dtype=float)


def _metrics_dict(m: HoldoutMetrics) -> dict[str, float]:
    return {"log_loss": m.log_loss, "brier": m.brier, "accuracy": m.accuracy}


def _train_calibrated_holdout(
    base: pd.DataFrame,
    platt_df: pd.DataFrame,
    holdout: pd.DataFrame,
    cols: list[str],
) -> tuple[Pipeline, PlattCalibrator, np.ndarray]:
    base_model = train_logistic(base, cols)
    platt = PlattCalibrator()
    raw_platt = base_model.predict_proba(platt_df[cols].values)[:, 1]
    platt.fit(raw_platt, platt_df["home_win"].values)
    raw_holdout = base_model.predict_proba(holdout[cols].values)[:, 1]
    cal_holdout = platt.transform(raw_holdout)
    return base_model, platt, cal_holdout


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

    cols = list(FEATURE_COLUMNS_V2)
    model = train_gbr(base, cols)
    raw_val = model.predict_proba(platt_df[cols].values)[:, 1]
    raw_holdout = model.predict_proba(holdout[cols].values)[:, 1]
    elo_val = elo_probs_from_pre(platt_df)
    elo_holdout = elo_probs_from_pre(holdout)
    tossup_cut = tune_tossup_cut(platt_df["home_win"].values, raw_val, elo_val)
    blended_holdout = apply_elo_tossup(raw_holdout, elo_holdout, tossup_cut)
    model_metrics = compute_metrics(MODEL_VERSION, y_holdout, blended_holdout)

    n_before_holdout = len(feat_all[feat_all["season"] != HOLDOUT_SEASON])
    elo_probs = predict_elo(feat_all)[n_before_holdout:]
    home_rate_probs = predict_home_rate_constant(train_raw, holdout)
    home_rate_metrics = compute_metrics("naive_home_win_rate", y_holdout, home_rate_probs)
    elo_metrics = compute_metrics("elo_baseline", y_holdout, elo_probs)

    naive_ll = min(home_rate_metrics.log_loss, elo_metrics.log_loss)
    ll_pass = production_gate_passes(model_metrics.log_loss, naive_ll)
    acc_pass = model_metrics.accuracy >= ACCURACY_HARD_MIN
    gate_passes = ll_pass and acc_pass

    artifact = {
        "model": model,
        "platt_calibrator": None,
        "tossup_cut": tossup_cut,
        "model_version": MODEL_VERSION,
        "feature_set": FEATURE_SET,
        "feature_columns": cols,
        "rest_fill": rest_fill,
        "base_train_seasons": list(BASE_TRAIN_SEASONS),
        "platt_season": PLATT_SEASON,
        "holdout_season": HOLDOUT_SEASON,
        "train_home_win_rate": home_rate,
    }
    MODEL_ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    save_nfl_promotion("train_nfl_baseline", artifact)

    results: dict[str, Any] = {
        "base_train_seasons": list(BASE_TRAIN_SEASONS),
        "platt_season": PLATT_SEASON,
        "holdout_season": HOLDOUT_SEASON,
        "train_rows": len(base) + len(platt_df),
        "holdout_rows": len(holdout),
        "feature_set": FEATURE_SET,
        "production_model": MODEL_VERSION,
        "active_model": MODEL_VERSION,
        "tossup_cut": tossup_cut,
        "imputation": {"rest_days": rest_fill},
        "metrics": {
            m.name: _metrics_dict(m)
            for m in (model_metrics, home_rate_metrics, elo_metrics)
        },
        "phase_gate": {
            "rule": (
                "Holdout log loss must beat min(naive home-win-rate, Elo) "
                f"and accuracy >= {ACCURACY_HARD_MIN:.0%}."
            ),
            "best_naive_log_loss": naive_ll,
            "accuracy_hard_min": ACCURACY_HARD_MIN,
            "log_loss_passes": ll_pass,
            "accuracy_passes": acc_pass,
            "passes": gate_passes,
            "active_model": MODEL_VERSION,
        },
        "active_holdout": _metrics_dict(model_metrics),
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
