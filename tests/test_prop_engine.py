"""Unit tests for the quantitative prop engine."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.prop_engine.constants import MIN_PROP_SCORE
from app.services.prop_engine.evaluate import evaluate_prop
from app.services.prop_engine.probabilities import model_probabilities
from app.services.prop_engine.projections import projection_supports_side


def test_projection_supports_side():
    assert projection_supports_side(0.8, 1.5, "under") is True
    assert projection_supports_side(2.0, 1.5, "over") is True
    assert projection_supports_side(0.8, 1.5, "over") is False


def test_model_probabilities_sum_near_one():
    probs = model_probabilities(1.5, projection=1.8, std_dev=0.9)
    total = probs["model_probability_over"] + probs["model_probability_under"]
    assert 0.95 <= total <= 1.05


@patch("app.services.prop_engine.evaluate._search_player_id", return_value=592450)
@patch("app.services.prop_engine.evaluate._season_game_log_values")
def test_evaluate_prop_scores_both_sides(mock_logs, _pid):
    mock_logs.return_value = tuple([2, 2, 1, 3, 2, 2, 1, 2, 3, 2])
    result = evaluate_prop(
        player="Aaron Judge",
        market_type="batter_hits",
        line=1.5,
        over_odds=-110,
        under_odds=-110,
        season=2026,
    )
    assert result["prop_score_over"] is not None
    assert result["prop_score_under"] is not None
    assert result["model_projection"] is not None
    assert result["model_probability_over"] is not None
    assert result["market_probability_over"] is not None
    assert "debug" in result


@patch("app.services.prop_engine.evaluate._search_player_id", return_value=592450)
@patch("app.services.prop_engine.evaluate._season_game_log_values")
@patch("app.services.prop_engine.evaluate._alltime_game_log_values")
def test_evaluate_prop_rejects_without_edge(mock_alltime, mock_logs, _pid):
    mock_logs.return_value = tuple([0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
    mock_alltime.return_value = tuple([0] * 10)
    result = evaluate_prop(
        player="Aaron Judge",
        market_type="batter_home_runs",
        line=0.5,
        over_odds=-200,
        under_odds=None,
        season=2026,
    )
    assert result["actionable"] is False
    assert result["rejection_reasons"]


@patch("app.services.prop_engine.evaluate._search_player_id", return_value=592450)
@patch("app.services.prop_engine.evaluate._season_game_log_values")
@patch("app.services.prop_engine.evaluate._alltime_game_log_values")
def test_evaluate_prop_trap_one_sided(mock_alltime, mock_logs, _pid):
    mock_logs.return_value = tuple([0, 0, 0, 0, 0, 0, 0, 0, 0, 0])
    mock_alltime.return_value = tuple([0] * 10)
    result = evaluate_prop(
        player="Aaron Judge",
        market_type="batter_home_runs",
        line=0.5,
        over_odds=-200,
        under_odds=None,
        season=2026,
    )
    assert result["recommended_side"] == "over"
    assert result["actionable"] is False
    assert "only Over is listed" in (result["actionable_reason"] or "")
