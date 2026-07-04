"""Tests for UFC layoff and rounds display helpers."""

from __future__ import annotations

from datetime import date

from app.features.ufc_pregame import (
    estimate_rounds_expected,
    format_layoff_label,
    fighter_layoff_days,
)


def test_format_layoff_label_years():
    assert "3 year" in format_layoff_label(1100)
    assert "mo" in format_layoff_label(120)


def test_estimate_rounds_expected_book_line():
    out = estimate_rounds_expected(totals_line=2.5, model_prob_home=0.6)
    assert out["source"] == "book"
    assert out["label"] == "2.5"


def test_estimate_rounds_expected_heuristic():
    out = estimate_rounds_expected(model_prob_home=0.82, model_prob_away=0.18)
    assert out["source"] == "estimate"
    assert out["value"] == 1.5


def test_fighter_layoff_days_from_history():
    days = fighter_layoff_days("Magomed Ankalaev", date(2024, 1, 13))
    assert days is None or days > 0
