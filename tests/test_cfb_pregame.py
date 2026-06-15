"""CFB v2 pregame feature tests — neutral site, form, conference."""

import pandas as pd

from app.features.cfb_pregame import (
    FEATURE_COLUMNS_V2,
    FEATURE_COLUMNS_V3,
    _ConferenceTracker,
    build_features,
)
from app.models.cfb_baseline import _elo_expected, attach_elo_features


def _games_neutral_vs_home() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "game_id": "1",
                "date": "2024-09-07",
                "season": 2024,
                "home_team": "Georgia",
                "away_team": "Clemson",
                "home_score": 34,
                "away_score": 3,
                "home_win": 1,
                "home_rest_days": 7.0,
                "away_rest_days": 7.0,
                "home_b2b": 0,
                "away_b2b": 0,
                "neutral_site": 1,
                "conference_game": 0,
                "home_conference": "SEC",
                "away_conference": "ACC",
            },
            {
                "game_id": "2",
                "date": "2024-09-14",
                "season": 2024,
                "home_team": "Alabama",
                "away_team": "Wisconsin",
                "home_score": 28,
                "away_score": 14,
                "home_win": 1,
                "home_rest_days": 7.0,
                "away_rest_days": 7.0,
                "home_b2b": 0,
                "away_b2b": 0,
                "neutral_site": 0,
                "conference_game": 0,
                "home_conference": "SEC",
                "away_conference": "Big Ten",
            },
        ]
    )


def test_neutral_site_home_field_active_zero():
    feat = build_features(_games_neutral_vs_home(), attach_elo=False)
    neutral_row = feat[feat["game_id"] == "1"].iloc[0]
    home_row = feat[feat["game_id"] == "2"].iloc[0]
    assert neutral_row["neutral_site"] == 1
    assert neutral_row["home_field_active"] == 0
    assert home_row["home_field_active"] == 1


def test_elo_no_home_adv_on_neutral():
    games = _games_neutral_vs_home()
    feat = build_features(games, attach_elo=False)
    with_elo = attach_elo_features(feat, games_df=games)
    assert _elo_expected(1500, 1500, neutral=True) == 0.5
    assert _elo_expected(1500, 1500, neutral=False) > 0.5
    neutral = with_elo[with_elo["game_id"] == "1"].iloc[0]
    assert neutral["elo_home_pre"] == 1500


def test_last5_form_no_future_leakage():
    rows = []
    for i, result in enumerate([1, 0, 1, 1, 0, 1], start=1):
        rows.append(
            {
                "game_id": str(i),
                "date": f"2024-09-{6 + i:02d}",
                "season": 2024,
                "home_team": "TeamA",
                "away_team": f"Opp{i}",
                "home_score": 28 if result else 10,
                "away_score": 10 if result else 28,
                "home_win": result,
                "home_rest_days": 7.0,
                "away_rest_days": 7.0,
                "home_b2b": 0,
                "away_b2b": 0,
                "neutral_site": 0,
                "conference_game": 0,
                "home_conference": "Test",
                "away_conference": "Other",
            }
        )
    df = pd.DataFrame(rows)
    feat = build_features(df, attach_elo=False)
    last = feat[feat["game_id"] == "6"].iloc[0]
    assert last["home_last5_win_pct"] == 0.6


def test_conference_win_pct_diff():
    tracker = _ConferenceTracker()
    tracker.update(2024, "SEC", "ACC", 1)
    tracker.update(2024, "ACC", "SEC", 0)
    sec_pct = tracker.win_pct_before(2024, "SEC")
    acc_pct = tracker.win_pct_before(2024, "ACC")
    assert round(sec_pct, 2) == 1.0
    assert round(acc_pct, 2) == 0.0


def test_v3_feature_columns_present():
    feat = build_features(_games_neutral_vs_home(), attach_elo=False)
    for col in FEATURE_COLUMNS_V3:
        assert col in feat.columns


def test_v3_sp_plus_defaults_without_cache():
    feat = build_features(_games_neutral_vs_home(), attach_elo=False, sp_lookup={})
    row = feat.iloc[0]
    assert row["sp_plus_diff"] == 0.0
    assert row["sp_offense_diff"] == 0.0
    assert row["sp_defense_diff"] == 0.0
