"""Tests for NBA prediction detail / drivers."""

from __future__ import annotations

from app.services.nba_prediction_detail import build_prediction_drivers


def test_build_prediction_drivers_legacy_stub():
    assert build_prediction_drivers({}) == []
