from datetime import date

import pandas as pd
import pytest

from app.odds.odds_repository import games_to_ml_dataframe
from app.services.daily_board import _slate_rows


def test_games_to_ml_dataframe_includes_spread_fields():
    games = [
        {
            "home_team": "Detroit Tigers",
            "away_team": "Boston Red Sox",
            "home_ml": -150,
            "away_ml": 130,
            "home_spread_point": -1.5,
            "home_spread_american": -110,
            "away_spread_point": 1.5,
            "away_spread_american": -110,
            "commence_time": "2025-08-15T23:00:00Z",
        }
    ]
    df = games_to_ml_dataframe(games, "the_odds_api", date(2025, 8, 15))
    row = df.iloc[0]
    assert row["home_spread_point"] == -1.5
    assert row["home_spread_american"] == -110
    assert row["away_spread_point"] == 1.5
    assert row["away_spread_american"] == -110


def test_slate_plus_ev_spread_at_eight_percent_edge():
    merged = pd.DataFrame(
        [
            {
                "game_id": "1",
                "date": "2025-08-15",
                "home_team": "Detroit Tigers",
                "away_team": "Boston Red Sox",
                "model_prob_home": 0.55,
                "model_prob_away": 0.45,
                "home_ml": -120,
                "away_ml": 110,
                "home_spread_point": -1.5,
                "home_spread_american": -110,
                "away_spread_point": 1.5,
                "away_spread_american": -110,
                "model_prob_home_cover": 0.59,
                "model_prob_away_cover": 0.41,
            }
        ]
    )
    rows = _slate_rows(merged, has_odds=True, totals_by_game={}, min_edge=0.08)
    row = rows[0]
    assert row["market_prob_home_cover"] == pytest.approx(0.5, rel=1e-3)
    assert row["spread_edge_home"] == pytest.approx(0.09, rel=1e-3)
    assert row["plus_ev_spread"] is True
    assert row["spread_best_pick"]["side"] == "home"
    assert row["spread_best_pick"]["team"] == "Detroit Tigers"
    assert row["spread_best_pick"]["spread_point"] == -1.5


def test_slate_spread_no_plus_ev_below_threshold():
    merged = pd.DataFrame(
        [
            {
                "game_id": "1",
                "date": "2025-08-15",
                "home_team": "Detroit Tigers",
                "away_team": "Boston Red Sox",
                "model_prob_home": 0.55,
                "model_prob_away": 0.45,
                "home_ml": -120,
                "away_ml": 110,
                "home_spread_point": -1.5,
                "home_spread_american": -110,
                "away_spread_point": 1.5,
                "away_spread_american": -110,
                "model_prob_home_cover": 0.54,
                "model_prob_away_cover": 0.46,
            }
        ]
    )
    rows = _slate_rows(merged, has_odds=True, totals_by_game={}, min_edge=0.08)
    assert rows[0]["plus_ev_spread"] is False
    assert rows[0]["spread_best_pick"] is None
