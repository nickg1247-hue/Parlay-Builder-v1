"""NFL parlay payload tests."""

from app.parlay.nfl_parlay import top_parlays_payload


def test_parlay_payload_requires_odds_and_edge():
    slate = [
        {
            "game_id": "1",
            "matchup": "BAL @ KC",
            "home_team": "KC",
            "away_team": "BAL",
            "home_ml": -140,
            "away_ml": 120,
            "model_prob_home": 0.70,
            "model_prob_away": 0.30,
            "market_prob_home": 0.56,
            "market_prob_away": 0.44,
        },
        {
            "game_id": "2",
            "matchup": "MIA @ BUF",
            "home_team": "BUF",
            "away_team": "MIA",
            "home_ml": -160,
            "away_ml": 140,
            "model_prob_home": 0.72,
            "model_prob_away": 0.28,
            "market_prob_home": 0.58,
            "market_prob_away": 0.42,
        },
        {
            "game_id": "3",
            "matchup": "NYJ @ NE",
            "home_team": "NE",
            "away_team": "NYJ",
            "home_ml": -110,
            "away_ml": -110,
            "model_prob_home": 0.66,
            "model_prob_away": 0.34,
            "market_prob_home": 0.50,
            "market_prob_away": 0.50,
        },
    ]
    parlays = top_parlays_payload(slate, min_edge=0.08)
    assert parlays
    first = parlays[0]
    assert first["num_legs"] >= 2
    assert first["ev"] > 0
    assert all(leg["game_id"] for leg in first["legs"])


def test_parlay_payload_empty_without_odds():
    assert top_parlays_payload([{"game_id": "1", "home_ml": None, "away_ml": None}]) == []
