"""Tests for shared production manifest + artifact pipeline."""

from __future__ import annotations

import json
from unittest.mock import patch

import joblib
import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app.models import production_pipeline as pp
from app.models.mlb_baseline import load_model_artifact
from app.services.model_lab import _promote_lab_run


@pytest.fixture
def isolated_pipeline(tmp_path, monkeypatch):
    models_dir = tmp_path / "models"
    monkeypatch.setattr(pp, "MODELS_DIR", models_dir)
    monkeypatch.setattr(pp, "ACTIVE_MONEYLINE_MANIFEST", tmp_path / "active_model.json")
    monkeypatch.setattr(
        pp, "ACTIVE_TOTALS_MANIFEST", tmp_path / "active_totals_model.json"
    )
    monkeypatch.setattr(
        pp, "LEGACY_MONEYLINE_ARTIFACT", tmp_path / "mlb_baseline_model.joblib"
    )
    monkeypatch.setattr(
        pp, "LEGACY_TOTALS_ARTIFACT", tmp_path / "mlb_totals_model.joblib"
    )
    monkeypatch.setattr(pp, "PROJECT_ROOT", tmp_path)
    return tmp_path


def _dummy_moneyline_artifact() -> dict:
    model = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(max_iter=200)),
        ]
    )
    x = np.random.randn(40, 3)
    y = (x[:, 0] > 0).astype(int)
    model.fit(x, y)
    return {
        "model": model,
        "model_version": "test_v3_platt",
        "feature_columns": ["f1", "f2", "f3"],
        "era_medians": {"default": 4.0},
        "rest_fill": 1.0,
        "platt_calibrator": None,
    }


def test_save_moneyline_promotion_writes_manifest(isolated_pipeline):
    artifact = _dummy_moneyline_artifact()
    manifest = pp.save_moneyline_promotion(
        "run-abc",
        artifact,
        feature_set="wave1_pruned",
    )
    assert manifest["run_id"] == "run-abc"
    assert manifest["feature_set"] == "wave1_pruned"
    assert manifest["model_version"] == "test_v3_platt"
    assert pp.ACTIVE_MONEYLINE_MANIFEST.exists()
    saved = json.loads(pp.ACTIVE_MONEYLINE_MANIFEST.read_text(encoding="utf-8"))
    assert saved["path"] == "models/run-abc.joblib"
    assert (isolated_pipeline / "models" / "run-abc.joblib").exists()


def test_load_active_artifact_prefers_manifest(isolated_pipeline, monkeypatch):
    artifact = _dummy_moneyline_artifact()
    pp.save_moneyline_promotion("run-xyz", artifact, feature_set="wave1_pruned")

    monkeypatch.setattr(
        "app.models.production_pipeline.load_active_artifact",
        pp.load_active_artifact,
    )
    loaded = pp.load_active_artifact("moneyline")
    assert loaded["model_version"] == "test_v3_platt"
    assert loaded["feature_columns"] == ["f1", "f2", "f3"]


def test_load_model_artifact_uses_active_manifest(isolated_pipeline, monkeypatch):
    artifact = _dummy_moneyline_artifact()
    pp.save_moneyline_promotion("run-live", artifact, feature_set="lab_set")

    monkeypatch.setattr(
        "app.models.production_pipeline.load_active_artifact",
        pp.load_active_artifact,
    )
    monkeypatch.setattr(
        "app.models.mlb_baseline.MODEL_ARTIFACT",
        isolated_pipeline / "missing_legacy.joblib",
    )
    loaded = load_model_artifact()
    assert loaded["model_version"] == "test_v3_platt"


def test_migrate_legacy_manifest(isolated_pipeline):
    legacy = _dummy_moneyline_artifact()
    joblib.dump(legacy, pp.LEGACY_MONEYLINE_ARTIFACT)
    pp.migrate_legacy_manifests_if_needed()
    manifest = json.loads(pp.ACTIVE_MONEYLINE_MANIFEST.read_text(encoding="utf-8"))
    assert manifest["run_id"] == "legacy"
    assert manifest["source"] == "legacy_migration"


def test_promote_lab_run_gate_failure():
    run = {
        "id": "bad-run",
        "track": "moneyline",
        "feature_columns": ["home_pitcher_era"],
        "feature_set": "v1_baseline",
        "test_confirm": {
            "production_gate": {"active_gate_passed": False},
        },
    }
    with pytest.raises(ValueError, match="gate failed"):
        _promote_lab_run(run)


def test_promote_lab_run_moneyline(isolated_pipeline, monkeypatch):
    artifact = _dummy_moneyline_artifact()
    run = {
        "id": "good-run",
        "track": "moneyline",
        "feature_columns": ["f1", "f2", "f3"],
        "feature_set": "wave1_pruned",
        "test_confirm": {
            "production_gate": {"active_gate_passed": True},
        },
    }
    with (
        patch(
            "app.services.model_lab.build_moneyline_platt_artifact",
            return_value=artifact,
        ),
        patch(
            "app.services.model_lab.save_moneyline_promotion",
            side_effect=pp.save_moneyline_promotion,
        ),
        patch("app.services.model_lab.load_games", return_value=pd.DataFrame()),
    ):
        manifest = _promote_lab_run(run)
    assert manifest["run_id"] == "good-run"
    assert pp.ACTIVE_MONEYLINE_MANIFEST.exists()
