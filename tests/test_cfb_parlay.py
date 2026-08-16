"""CFB parlay payload tests."""

from app.parlay.cfb_parlay import top_parlays_payload


def test_cfb_parlay_payload_requires_odds_and_edge():
    slate = [
        {
            "game_id": "1",
            "date": "2024-11-30",
            "matchup": "Georgia Tech @ Georgia",
            "home_team": "Georgia",
            "away_team": "Georgia Tech",
            "home_ml": -350,
            "away_ml": 280,
            "model_prob_home": 0.82,
            "model_prob_away": 0.18,
            "market_prob_home": 0.72,
            "market_prob_away": 0.28,
        },
        {
            "game_id": "2",
            "date": "2024-11-30",
            "matchup": "Auburn @ Alabama",
            "home_team": "Alabama",
            "away_team": "Auburn",
            "home_ml": -280,
            "away_ml": 230,
            "model_prob_home": 0.80,
            "model_prob_away": 0.20,
            "market_prob_home": 0.68,
            "market_prob_away": 0.32,
        },
        {
            "game_id": "3",
            "date": "2024-11-30",
            "matchup": "Kentucky @ Tennessee",
            "home_team": "Tennessee",
            "away_team": "Kentucky",
            "home_ml": -200,
            "away_ml": 170,
            "model_prob_home": 0.74,
            "model_prob_away": 0.26,
            "market_prob_home": 0.62,
            "market_prob_away": 0.38,
        },
    ]
    parlays = top_parlays_payload(slate, min_edge=0.08)
    assert parlays
    first = parlays[0]
    assert first["num_legs"] >= 2
    assert first["ev"] > 0
    assert all(leg["game_id"] for leg in first["legs"])


def test_cfb_parlay_payload_empty_without_odds():
    assert top_parlays_payload([{"game_id": "1", "home_ml": None, "away_ml": None}]) == []
