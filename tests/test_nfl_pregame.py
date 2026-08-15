"""NFL pregame feature leakage and rest tests."""

import pandas as pd

from app.features.nfl_pregame import FEATURE_COLUMNS, build_features
from app.models.nfl_baseline import production_gate_passes


def _game(
    gid: str,
    date: str,
    season: int,
    home: str,
    away: str,
    home_win: int,
    *,
    home_rest: float = 7.0,
    away_rest: float = 7.0,
    divisional: int = 0,
    neutral: int = 0,
    week: int = 1,
) -> dict:
    return {
        "game_id": gid,
        "date": date,
        "season": season,
        "week": week,
        "game_type": "regular",
        "home_team": home,
        "away_team": away,
        "home_team_abbr": home,
        "away_team_abbr": away,
        "home_score": 24 if home_win else 17,
        "away_score": 17 if home_win else 24,
        "home_win": home_win,
        "home_rest_days": home_rest,
        "away_rest_days": away_rest,
        "divisional": divisional,
        "neutral_site": neutral,
    }


def test_feature_columns_present():
    df = pd.DataFrame(
        [
            _game("1", "2024-09-08", 2024, "KC", "BAL", 1),
            _game("2", "2024-09-15", 2024, "BAL", "KC", 0, week=2),
        ]
    )
    feat = build_features(df, attach_elo=True)
    for col in FEATURE_COLUMNS:
        assert col in feat.columns


def test_season_win_pct_no_same_game_leakage():
    df = pd.DataFrame(
        [
            _game("1", "2024-09-08", 2024, "KC", "BAL", 1, week=1),
            _game("2", "2024-09-15", 2024, "KC", "CIN", 1, week=2),
        ]
    )
    feat = build_features(df, attach_elo=False)
    week1 = feat[feat["game_id"] == "1"].iloc[0]
    week2 = feat[feat["game_id"] == "2"].iloc[0]
    assert week1["home_season_win_pct"] == 0.5
    assert week2["home_season_win_pct"] == 1.0


def test_elo_uses_prior_games_only():
    df = pd.DataFrame(
        [
            _game("1", "2024-09-08", 2024, "KC", "BAL", 1, week=1),
            _game("2", "2024-09-15", 2024, "KC", "CIN", 1, week=2),
        ]
    )
    feat = build_features(df, attach_elo=True)
    week1 = feat[feat["game_id"] == "1"].iloc[0]
    week2 = feat[feat["game_id"] == "2"].iloc[0]
    assert week1["elo_home_pre"] == 1500.0
    assert week2["elo_home_pre"] > 1500.0


def test_short_week_rest_diff():
    df = pd.DataFrame(
        [
            _game("1", "2024-09-08", 2024, "KC", "BAL", 1, week=1),
            _game("2", "2024-09-12", 2024, "KC", "CIN", 1, home_rest=4.0, away_rest=7.0, week=2),
        ]
    )
    feat = build_features(df, attach_elo=False)
    week2 = feat[feat["game_id"] == "2"].iloc[0]
    assert week2["home_rest_days"] == 4.0
    assert week2["rest_diff"] == -3.0


def test_neutral_site_clears_home_field():
    df = pd.DataFrame(
        [
            _game("1", "2024-10-13", 2024, "NYJ", "MIN", 1, neutral=1),
            _game("2", "2024-10-20", 2024, "KC", "SF", 1, neutral=0),
        ]
    )
    feat = build_features(df, attach_elo=False)
    assert feat[feat["game_id"] == "1"].iloc[0]["home_field"] == 0
    assert feat[feat["game_id"] == "2"].iloc[0]["home_field"] == 1


def test_is_preseason_flag():
    df = pd.DataFrame(
        [
            {**_game("1", "2024-08-08", 2024, "KC", "BAL", 1), "game_type": "preseason"},
            _game("2", "2024-09-08", 2024, "KC", "CIN", 1, week=1),
        ]
    )
    feat = build_features(df, attach_elo=False)
    assert feat[feat["game_id"] == "1"].iloc[0]["is_preseason"] == 1
    assert feat[feat["game_id"] == "2"].iloc[0]["is_preseason"] == 0


def test_scoring_features_no_same_game_leakage():
    from app.features.nfl_pregame import MARGIN_FEATURE_COLUMNS

    df = pd.DataFrame(
        [
            _game("1", "2024-09-08", 2024, "KC", "BAL", 1, week=1),
            _game("2", "2024-09-15", 2024, "KC", "CIN", 1, week=2),
        ]
    )
    feat = build_features(df, attach_elo=False, include_scoring=True)
    for col in MARGIN_FEATURE_COLUMNS:
        assert col in feat.columns
    week1 = feat[feat["game_id"] == "1"].iloc[0]
    week2 = feat[feat["game_id"] == "2"].iloc[0]
    assert week1["home_season_pts_for"] == 22.0
    assert week2["home_season_pts_for"] == 24.0


def test_production_gate_math():
    assert production_gate_passes(0.60, 0.65) is True
    assert production_gate_passes(0.65, 0.60) is False
    assert production_gate_passes(0.60, 0.60) is False
