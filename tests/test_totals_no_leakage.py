import pandas as pd

from app.features.mlb_totals_pregame import build_totals_features


def test_totals_rolling_excludes_current_and_future_games():
    games = pd.DataFrame(
        [
            {
                "game_id": "1",
                "date": "2025-06-01",
                "home_team": "A",
                "away_team": "B",
                "season": 2025,
                "home_score": 5,
                "away_score": 3,
                "total_runs": 8,
            },
            {
                "game_id": "2",
                "date": "2025-06-02",
                "home_team": "A",
                "away_team": "C",
                "season": 2025,
                "home_score": 2,
                "away_score": 1,
                "total_runs": 3,
            },
            {
                "game_id": "3",
                "date": "2025-06-03",
                "home_team": "A",
                "away_team": "D",
                "season": 2025,
                "home_score": 4,
                "away_score": 4,
                "total_runs": 8,
            },
        ]
    )
    feats = build_totals_features(games)
    row2 = feats[feats["game_id"] == "2"].iloc[0]
    assert row2["home_season_runs_scored_pg"] == 5.0
    row3 = feats[feats["game_id"] == "3"].iloc[0]
    assert row3["home_season_runs_scored_pg"] == 3.5
