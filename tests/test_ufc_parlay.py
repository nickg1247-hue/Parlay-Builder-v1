"""Tests for UFC cross-fight parlay ranker."""

from __future__ import annotations

from app.parlay.ufc_parlay import top_parlays_payload


def test_top_parlays_payload_empty_without_odds():
    slate = [
        {
            "fight_id": "1",
            "home_team": "A",
            "away_team": "B",
            "model_prob_home": 0.6,
            "model_prob_away": 0.4,
        }
    ]
    assert top_parlays_payload(slate) == []


def test_top_parlays_payload_ranks_legs():
    slate = [
        {
            "fight_id": "1",
            "date": "2024-01-13",
            "matchup": "B vs A",
            "home_team": "A",
            "away_team": "B",
            "home_ml": -150,
            "away_ml": 130,
            "model_prob_home": 0.7,
            "model_prob_away": 0.3,
            "market_prob_home": 0.55,
            "market_prob_away": 0.45,
        },
        {
            "fight_id": "2",
            "date": "2024-01-13",
            "matchup": "D vs C",
            "home_team": "C",
            "away_team": "D",
            "home_ml": -120,
            "away_ml": 100,
            "model_prob_home": 0.62,
            "model_prob_away": 0.38,
            "market_prob_home": 0.5,
            "market_prob_away": 0.5,
        },
    ]
    parlays = top_parlays_payload(slate, min_edge=0.05, max_parlays=3, min_legs=2)
    assert parlays
    assert parlays[0]["num_legs"] >= 2
    assert parlays[0]["ev"] > 0
