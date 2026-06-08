"""Known small schedules → expected Wave 1 feature values."""

from unittest.mock import patch

import pandas as pd

from app.features.mlb_pregame import (
    NEUTRAL_RUN_DIFF,
    NEUTRAL_WIN_PCT,
    build_features,
)


def _fake_pitcher_profile(name, season, era_medians, default_whip=1.3, default_ip=150.0):
    return {"era": 4.0, "fip": None, "whip": 1.25, "ip": 180.0}


@patch("app.features.mlb_pregame.lookup_pitcher_profile", side_effect=_fake_pitcher_profile)
@patch(
    "app.features.mlb_pregame._load_park_factors",
    return_value={"TeamA": 1.05, "TeamB": 1.0, "TeamC": 1.0, "TeamD": 1.0},
)
def test_known_game_feature_values(_park, _pitcher):
    games = pd.DataFrame(
        [
            {
                "game_id": "1",
                "date": "2024-04-01",
                "home_team": "TeamA",
                "away_team": "TeamB",
                "season": 2024,
                "home_score": 4,
                "away_score": 1,
                "home_win": 1,
            },
            {
                "game_id": "2",
                "date": "2024-04-02",
                "home_team": "TeamC",
                "away_team": "TeamA",
                "season": 2024,
                "home_score": 5,
                "away_score": 2,
                "home_win": 1,
            },
            {
                "game_id": "3",
                "date": "2024-04-03",
                "home_team": "TeamA",
                "away_team": "TeamD",
                "season": 2024,
                "home_score": 3,
                "away_score": 2,
                "home_win": 1,
            },
        ]
    )
    feats = build_features(games, era_medians={"default": 4.0})
    row = feats[feats["game_id"] == "3"].iloc[0]

    assert row["home_season_win_pct"] == 0.5
    assert row["home_season_run_diff"] == 0.0
    assert row["away_season_win_pct"] == NEUTRAL_WIN_PCT
    assert row["away_season_run_diff"] == NEUTRAL_RUN_DIFF
    assert row["home_last30_win_pct"] == 0.5
    assert row["home_home_split_win_pct"] == 1.0
    assert row["away_away_split_win_pct"] == NEUTRAL_WIN_PCT
    assert row["park_factor_runs"] == 1.05
    assert row["home_pitcher_whip"] == 1.25
    assert row["home_pitcher_ip"] == 180.0
    # TeamC 1.0 → rank 1; TeamA / TeamD tied at 0.5 → ranks 2–3; TeamB 0.0 → rank 4
    assert row["home_win_pct_rank"] in (2.0, 3.0)
    assert row["away_win_pct_rank"] in (2.0, 3.0)
