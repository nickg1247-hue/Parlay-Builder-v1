"""Shared production artifact build, manifest, and load for moneyline + totals."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import joblib
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor

from app.config import PROJECT_ROOT
from app.features.mlb_pregame import (
    FEATURE_COLUMNS_WAVE1,
    build_features_for_history,
)
from app.features.mlb_totals_pregame import (
    TOTALS_FEATURE_COLUMNS,
    build_totals_features_for_history,
)
from app.models.mlb_baseline import (
    DEFAULT_REST_DAYS,
    NEUTRAL_LAST10_RUN_DIFF,
    NEUTRAL_LAST10_WIN_PCT,
    _season_era_medians,
    attach_elo_features,
    load_games,
    train_logistic,
)
from app.models.mlb_totals import LEAGUE_AVG_TOTAL, pick_margin
from app.models.platt_calibration import PlattCalibrator

MODELS_DIR = PROJECT_ROOT / "data" / "processed" / "models"
ACTIVE_MONEYLINE_MANIFEST = PROJECT_ROOT / "data" / "processed" / "active_model.json"
ACTIVE_TOTALS_MANIFEST = PROJECT_ROOT / "data" / "processed" / "active_totals_model.json"
LEGACY_MONEYLINE_ARTIFACT = PROJECT_ROOT / "data" / "processed" / "mlb_baseline_model.joblib"
LEGACY_TOTALS_ARTIFACT = PROJECT_ROOT / "data" / "processed" / "mlb_totals_model.joblib"

PLATT_TRAIN_SEASON = 2023
PLATT_CAL_SEASON = 2025
TRAIN_SEASONS = (2023, 2024, 2025)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rel_path(path: Path) -> str:
    return path.relative_to(PROJECT_ROOT).as_posix()


def _resolve_artifact_path(path_str: str) -> Path:
    path = Path(path_str)
    return path if path.is_absolute() else PROJECT_ROOT / path


def compute_imputation_params(raw: pd.DataFrame | None = None) -> tuple[dict, float]:
    """ERA medians and rest_fill from TRAIN_SEASONS games."""
    import math

    games = raw if raw is not None else load_games()
    train_raw = games[games["season"].isin(TRAIN_SEASONS)].copy()
    era_medians = _season_era_medians(train_raw)
    rest_fill = float(
        pd.concat([train_raw["home_rest_days"], train_raw["away_rest_days"]])
        .dropna()
        .median()
    )
    if math.isnan(rest_fill):
        rest_fill = float(DEFAULT_REST_DAYS)
    return era_medians, rest_fill


def _feature_frame_for_moneyline(
    raw: pd.DataFrame,
    feature_columns: list[str],
) -> pd.DataFrame:
    feat_all = build_features_for_history(raw)
    if any(c.startswith("elo_") for c in feature_columns):
        feat_all = attach_elo_features(feat_all)
    missing = [c for c in feature_columns if c not in feat_all.columns]
    if missing:
        raise ValueError(f"Moneyline feature columns missing from frame: {missing}")
    return feat_all


def build_moneyline_platt_artifact(
    feature_columns: list[str],
    *,
    raw: pd.DataFrame | None = None,
    model_version: str = "v3_logistic_pruned_platt",
    wave1_pruned_columns: list[str] | None = None,
    wave1_dropped_columns: list[str] | None = None,
) -> dict[str, Any]:
    """
    Production moneyline artifact: logistic base trained on 2023, Platt fit on 2024.
    """
    games = raw if raw is not None else load_games()
    era_medians, rest_fill = compute_imputation_params(games)
    feat_all = _feature_frame_for_moneyline(games, feature_columns)

    train_2023 = feat_all[feat_all["season"] == PLATT_TRAIN_SEASON].copy()
    cal_2024 = feat_all[feat_all["season"] == PLATT_CAL_SEASON].copy()
    if train_2023.empty or cal_2024.empty:
        raise ValueError(
            f"Need seasons {PLATT_TRAIN_SEASON} and {PLATT_CAL_SEASON} for Platt pipeline"
        )

    platt_base, _ = train_logistic(
        train_2023,
        cal_2024,
        feature_columns,
        metrics_name="platt_base_2023",
    )
    platt = PlattCalibrator()
    raw_cal = platt_base.predict_proba(cal_2024[feature_columns].values)[:, 1]
    platt.fit(raw_cal, cal_2024["home_win"].values)

    pruned_cols = wave1_pruned_columns
    if pruned_cols is None and set(feature_columns) <= set(FEATURE_COLUMNS_WAVE1):
        pruned_cols = list(feature_columns)

    return {
        "model": platt_base,
        "model_version": model_version,
        "feature_columns": list(feature_columns),
        "era_medians": era_medians,
        "rest_fill": rest_fill,
        "neutral_last10_win_pct": NEUTRAL_LAST10_WIN_PCT,
        "neutral_last10_run_diff": NEUTRAL_LAST10_RUN_DIFF,
        "platt_calibrator": platt,
        "wave1_pruned_columns": pruned_cols,
        "wave1_dropped_columns": wave1_dropped_columns or [],
    }


def build_totals_artifact(
    feature_columns: list[str],
    *,
    raw: pd.DataFrame | None = None,
    model_version: str = "v1_gbr_poisson",
) -> dict[str, Any]:
    """Production totals artifact trained on seasons ≤ 2024."""
    from app.data.mlb_games import load_games_with_totals

    games = raw if raw is not None else load_games_with_totals()
    era_medians, rest_fill = compute_imputation_params(games)
    feat = build_totals_features_for_history(games)
    train = feat[feat["season"].isin(TRAIN_SEASONS)].copy()
    missing = [c for c in feature_columns if c not in train.columns]
    if missing:
        raise ValueError(f"Totals feature columns missing from frame: {missing}")

    reg = GradientBoostingRegressor(
        n_estimators=120,
        max_depth=3,
        learning_rate=0.08,
        random_state=42,
    )
    reg.fit(train[feature_columns].values, train["total_runs"].values)

    return {
        "model": reg,
        "model_version": model_version,
        "feature_columns": list(feature_columns),
        "era_medians": era_medians,
        "rest_fill": rest_fill,
        "league_avg_total": LEAGUE_AVG_TOTAL,
        "pick_margin": pick_margin(),
    }


def _write_manifest(
    manifest_path: Path,
    *,
    track: Literal["moneyline", "totals"],
    run_id: str,
    artifact_path: Path,
    feature_set: str,
    model_version: str,
    source: str | None = None,
) -> dict[str, Any]:
    manifest = {
        "track": track,
        "run_id": run_id,
        "path": _rel_path(artifact_path),
        "feature_set": feature_set,
        "model_version": model_version,
        "promoted_at": _iso_now(),
    }
    if source:
        manifest["source"] = source
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def save_moneyline_promotion(
    run_id: str,
    artifact: dict[str, Any],
    *,
    feature_set: str,
) -> dict[str, Any]:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    artifact_path = MODELS_DIR / f"{run_id}.joblib"
    joblib.dump(artifact, artifact_path)
    return _write_manifest(
        ACTIVE_MONEYLINE_MANIFEST,
        track="moneyline",
        run_id=run_id,
        artifact_path=artifact_path,
        feature_set=feature_set,
        model_version=artifact["model_version"],
    )


def save_totals_promotion(
    run_id: str,
    artifact: dict[str, Any],
    *,
    feature_set: str,
) -> dict[str, Any]:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    artifact_path = MODELS_DIR / f"{run_id}_totals.joblib"
    joblib.dump(artifact, artifact_path)
    return _write_manifest(
        ACTIVE_TOTALS_MANIFEST,
        track="totals",
        run_id=run_id,
        artifact_path=artifact_path,
        feature_set=feature_set,
        model_version=artifact["model_version"],
    )


def load_active_manifest(track: Literal["moneyline", "totals"]) -> dict[str, Any] | None:
    path = ACTIVE_MONEYLINE_MANIFEST if track == "moneyline" else ACTIVE_TOTALS_MANIFEST
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def migrate_legacy_manifests_if_needed() -> None:
    """Point active manifests at legacy joblibs when no manifest exists yet."""
    if not ACTIVE_MONEYLINE_MANIFEST.exists() and LEGACY_MONEYLINE_ARTIFACT.exists():
        artifact = joblib.load(LEGACY_MONEYLINE_ARTIFACT)
        _write_manifest(
            ACTIVE_MONEYLINE_MANIFEST,
            track="moneyline",
            run_id="legacy",
            artifact_path=LEGACY_MONEYLINE_ARTIFACT,
            feature_set="train_mlb_baseline",
            model_version=artifact.get("model_version", "v1_logistic"),
            source="legacy_migration",
        )
    if not ACTIVE_TOTALS_MANIFEST.exists() and LEGACY_TOTALS_ARTIFACT.exists():
        artifact = joblib.load(LEGACY_TOTALS_ARTIFACT)
        _write_manifest(
            ACTIVE_TOTALS_MANIFEST,
            track="totals",
            run_id="legacy",
            artifact_path=LEGACY_TOTALS_ARTIFACT,
            feature_set="train_mlb_totals",
            model_version=artifact.get("model_version", "v1_gbr_poisson"),
            source="legacy_migration",
        )


def load_active_artifact(track: Literal["moneyline", "totals"]) -> dict[str, Any]:
    migrate_legacy_manifests_if_needed()
    manifest = load_active_manifest(track)
    if manifest is not None:
        artifact_path = _resolve_artifact_path(manifest["path"])
        if artifact_path.exists():
            return joblib.load(artifact_path)
    legacy = LEGACY_MONEYLINE_ARTIFACT if track == "moneyline" else LEGACY_TOTALS_ARTIFACT
    if legacy.exists():
        return joblib.load(legacy)
    label = "moneyline" if track == "moneyline" else "totals"
    raise FileNotFoundError(
        f"No active {label} model manifest or legacy artifact found."
    )


def get_active_model_info(track: Literal["moneyline", "totals"]) -> dict[str, Any] | None:
    migrate_legacy_manifests_if_needed()
    manifest = load_active_manifest(track)
    if manifest is None:
        legacy = (
            LEGACY_MONEYLINE_ARTIFACT if track == "moneyline" else LEGACY_TOTALS_ARTIFACT
        )
        if not legacy.exists():
            return None
        artifact = joblib.load(legacy)
        return {
            "track": track,
            "run_id": "legacy",
            "feature_set": "legacy_artifact",
            "model_version": artifact.get("model_version"),
            "promoted_at": None,
            "path": _rel_path(legacy),
            "source": "legacy_fallback",
        }
    return {
        "track": manifest.get("track", track),
        "run_id": manifest.get("run_id"),
        "feature_set": manifest.get("feature_set"),
        "model_version": manifest.get("model_version"),
        "promoted_at": manifest.get("promoted_at"),
        "path": manifest.get("path"),
        "source": manifest.get("source"),
    }
