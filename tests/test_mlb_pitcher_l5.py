"""Pitcher last-5-start (L5) features — no leakage, hand-checked ERA/WHIP."""

from unittest.mock import patch

import pandas as pd
import pytest

from app.data.pitcher_form import pitcher_last_n_starts, pitcher_l5_rates
from app.features.mlb_pregame import build_features


def _starter_log() -> pd.DataFrame:
    rows = []
    for i in range(6):
        rows.append(
            {
                "game_id": str(i + 1),
                "date": pd.Timestamp(f"2024-04-{i + 1:02d}"),
                "season": 2024,
                "team": "TeamA",
                "pitcher_name": "Ace Pitcher",
                "pitcher_key": "ace pitcher",
                "ip": 6.0,
                "er": i,
                "hits": 4,
                "walks": 1,
                "is_starter": True,
            }
        )
    return pd.DataFrame(rows)


def test_pitcher_l5_excludes_current_game_and_uses_last_five():
    log = _starter_log()
    before = pd.Timestamp("2024-04-07")
    starts = pitcher_last_n_starts("Ace Pitcher", before, 2024, log, n=5)
    assert len(starts) == 5
    assert starts["date"].max() == pd.Timestamp("2024-04-06")
    assert pd.Timestamp("2024-04-01") not in set(starts["date"])

    rates = pitcher_l5_rates("Ace Pitcher", before, 2024, log)
    assert rates is not None
    # Last 5 starts: ER 1+2+3+4+5=15, IP=30 → ERA=4.5
    assert rates["era"] == pytest.approx(4.5, rel=1e-4)
    # WHIP: (4+1)*5 / 30 = 25/30
    assert rates["whip"] == pytest.approx(25 / 30, rel=1e-4)


def test_pitcher_l5_same_day_start_never_in_window():
    log = _starter_log()
    game_day = pd.Timestamp("2024-04-06")
    starts = pitcher_last_n_starts("Ace Pitcher", game_day, 2024, log, n=5)
    assert pd.Timestamp("2024-04-06") not in set(starts["date"])
    assert len(starts) == 5


@patch("app.features.mlb_pregame.lookup_pitcher_profile")
@patch("app.features.mlb_pregame._load_park_factors", return_value={})
def test_l5_leakage_unchanged_when_game_appended_to_log(_park, _profile):
    _profile.side_effect = lambda name, season, medians, dw=1.3, di=150: {
        "era": 4.0,
        "fip": None,
        "whip": 1.25,
        "ip": 180.0,
    }
    log = _starter_log()
    games = pd.DataFrame(
        [
            {
                "game_id": "7",
                "date": "2024-04-07",
                "home_team": "TeamA",
                "away_team": "TeamB",
                "season": 2024,
                "home_score": 3,
                "away_score": 2,
                "home_win": 1,
                "home_starting_pitcher": "Ace Pitcher",
                "away_starting_pitcher": "Other Pitcher",
            }
        ]
    )
    with patch("app.features.mlb_pregame.get_pitcher_game_log", return_value=log):
        feats_before = build_features(games, era_medians={"default": 4.0})
    # Simulate post-game log append (would leak if same-day included)
    appended = pd.concat(
        [
            log,
            pd.DataFrame(
                [
                    {
                        "game_id": "7",
                        "date": pd.Timestamp("2024-04-07"),
                        "season": 2024,
                        "team": "TeamA",
                        "pitcher_name": "Ace Pitcher",
                        "pitcher_key": "ace pitcher",
                        "ip": 7.0,
                        "er": 9,
                        "hits": 10,
                        "walks": 3,
                        "is_starter": True,
                    }
                ]
            ),
        ],
        ignore_index=True,
    )
    with patch("app.features.mlb_pregame.get_pitcher_game_log", return_value=appended):
        feats_after = build_features(games, era_medians={"default": 4.0})
    assert feats_before.iloc[0]["home_pitcher_era_l5"] == feats_after.iloc[0][
        "home_pitcher_era_l5"
    ]
    assert feats_before.iloc[0]["home_pitcher_whip_l5"] == feats_after.iloc[0][
        "home_pitcher_whip_l5"
    ]
