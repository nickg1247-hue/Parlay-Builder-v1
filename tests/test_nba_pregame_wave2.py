"""NBA pregame wave-2 scoring feature tests."""

import pandas as pd

from app.features import nba_pregame as nfp


def _two_game_slate() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "game_id": "g1",
                "date": "2023-10-24",
                "season": 2024,
                "game_type": "regular",
                "home_team": "Boston Celtics",
                "away_team": "New York Knicks",
                "home_score": 108,
                "away_score": 104,
                "home_win": 1,
                "home_rest_days": 2.0,
                "away_rest_days": 2.0,
                "home_b2b": 0,
                "away_b2b": 0,
            },
            {
                "game_id": "g2",
                "date": "2023-10-26",
                "season": 2024,
                "game_type": "regular",
                "home_team": "Boston Celtics",
                "away_team": "Miami Heat",
                "home_score": 110,
                "away_score": 100,
                "home_win": 1,
                "home_rest_days": 2.0,
                "away_rest_days": 1.0,
                "home_b2b": 0,
                "away_b2b": 0,
            },
        ]
    )


def test_wave2_column_count():
    assert len(nfp.FEATURE_COLUMNS_V1) == 10
    assert len(nfp.FEATURE_COLUMNS_WAVE2) == 22
    assert nfp.FEATURE_COLUMNS == nfp.FEATURE_COLUMNS_WAVE2


def test_scoring_no_leakage_game1_neutral_game2_uses_game1():
    pts_fill = 110.0
    feats = nfp.build_features(_two_game_slate(), rest_fill=2.0, pts_fill=pts_fill)
    g1 = feats[feats["game_id"] == "g1"].iloc[0]
    g2 = feats[feats["game_id"] == "g2"].iloc[0]

    assert g1["home_last10_pts_for"] == pts_fill
    assert g1["home_last10_pts_against"] == pts_fill
    assert g1["home_last10_margin_avg"] == 0.0

    assert g2["home_last10_pts_for"] == 108.0
    assert g2["home_last10_pts_against"] == 104.0
    assert g2["home_last10_margin_avg"] == 4.0


def test_rest_diff_and_pace_proxy():
    feats = nfp.build_features(_two_game_slate(), rest_fill=2.0, pts_fill=110.0)
    g2 = feats[feats["game_id"] == "g2"].iloc[0]
    assert g2["rest_diff"] == 1.0
    assert g2["matchup_pace_proxy"] == 108.0 + 110.0


def test_build_features_for_slate_emits_scoring_columns():
    history = _two_game_slate()
    slate = pd.DataFrame(
        [
            {
                "game_id": "g3",
                "date": "2023-10-28",
                "season": 2024,
                "game_type": "regular",
                "home_team": "Boston Celtics",
                "away_team": "Los Angeles Lakers",
                "home_rest_days": 2.0,
                "away_rest_days": 2.0,
                "home_b2b": 0,
                "away_b2b": 0,
            }
        ]
    )
    feats = nfp.build_features_for_slate(slate, history_df=history)
    for col in nfp.SCORING_FEATURE_COLUMNS:
        assert col in feats.columns
