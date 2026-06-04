"""Ensure rolling team stats never include the current or future game."""

import pandas as pd

from app.features.mlb_pregame import build_features


def test_future_games_excluded_from_rolling_stats():
    games = pd.DataFrame(
        [
            {
                "game_id": "g1",
                "date": "2025-06-01",
                "home_team": "TeamA",
                "away_team": "TeamB",
                "season": 2025,
                "home_score": 5,
                "away_score": 2,
                "home_win": 1,
            },
            {
                "game_id": "g2",
                "date": "2025-06-02",
                "home_team": "TeamA",
                "away_team": "TeamC",
                "season": 2025,
                "home_score": 1,
                "away_score": 4,
                "home_win": 0,
            },
            {
                "game_id": "g3",
                "date": "2025-06-03",
                "home_team": "TeamA",
                "away_team": "TeamD",
                "season": 2025,
                "home_score": 3,
                "away_score": 3,
                "home_win": 0,
            },
        ]
    )

    feats = build_features(games)
    row_g2 = feats[feats["game_id"] == "g2"].iloc[0]
    assert row_g2["home_season_win_pct"] == 1.0
    assert row_g2["home_season_run_diff"] == 3.0

    row_g3 = feats[feats["game_id"] == "g3"].iloc[0]
    assert row_g3["home_season_win_pct"] == 0.5
    assert row_g3["home_season_run_diff"] == 0.0


def test_same_day_second_game_excludes_first_game_same_calendar_day():
    games = pd.DataFrame(
        [
            {
                "game_id": "am",
                "date": "2025-07-04",
                "home_team": "TeamA",
                "away_team": "TeamB",
                "season": 2025,
                "home_score": 6,
                "away_score": 1,
                "home_win": 1,
            },
            {
                "game_id": "pm",
                "date": "2025-07-04",
                "home_team": "TeamA",
                "away_team": "TeamC",
                "season": 2025,
                "home_score": 0,
                "away_score": 2,
                "home_win": 0,
            },
        ]
    )
    feats = build_features(games)
    pm = feats[feats["game_id"] == "pm"].iloc[0]
    assert pm["home_season_win_pct"] == 0.5
