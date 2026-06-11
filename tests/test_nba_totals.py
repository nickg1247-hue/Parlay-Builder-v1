"""Tests for NBA totals model."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from app.models import nba_totals as nt


def test_prob_over_normal_half_point():
    # Expected 230, std 18, line 224.5 -> high over prob
    p = nt.prob_over_normal(230.0, 18.0, 224.5)
    assert p > 0.6


def test_actual_went_over_half_point():
    assert nt.actual_went_over(225.0, 224.5) == 1
    assert nt.actual_went_over(224.0, 224.5) == 0


def test_totals_production_gate():
    assert nt.totals_production_gate_passes(0.70, 0.68, 14.0, 15.0) is False
    assert nt.totals_production_gate_passes(0.65, 0.68, 16.0, 15.0) is False
    assert nt.totals_production_gate_passes(0.65, 0.68, 14.0, 15.0) is True
    assert nt.totals_production_gate_passes(None, 0.68, 14.0, 15.0) is False


def test_enrich_totals_columns_adds_expected(monkeypatch):
    artifact = {
        "model": type(
            "M",
            (),
            {"predict": lambda self, x: np.array([228.5])},
        )(),
        "feature_columns": ["home_last10_pts_for"],
        "total_std": 18.0,
    }

    def fake_load():
        return artifact

    def fake_build(df):
        out = df.copy()
        out["home_last10_pts_for"] = 110.0
        return out

    monkeypatch.setattr(nt, "load_totals_artifact", fake_load)
    monkeypatch.setattr(nt, "build_features_for_slate", fake_build)

    df = pd.DataFrame(
        [
            {
                "game_id": "1",
                "home_team": "Boston Celtics",
                "away_team": "New York Knicks",
                "ou_line": 224.5,
                "over_odds": -110,
                "under_odds": -110,
            }
        ]
    )
    out = nt.enrich_totals_columns(df)
    assert out.loc[0, "expected_total_pts"] == pytest.approx(228.5)
    assert out.loc[0, "model_prob_over"] is not None
    assert 0.0 < out.loc[0, "model_prob_over"] < 1.0
