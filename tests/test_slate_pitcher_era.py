from unittest.mock import patch

import pandas as pd

from app.parlay.slate import build_slate_dataframe


def test_live_slate_uses_different_pitcher_eras():
    fake_games = [
        {
            "gamePk": 999001,
            "gameDate": "2026-06-15T23:00:00Z",
            "officialDate": "2026-06-15",
            "status": {"abstractGameState": "Preview"},
            "teams": {
                "home": {
                    "team": {"name": "New York Yankees"},
                    "probablePitcher": {"fullName": "Gerrit Cole"},
                },
                "away": {
                    "team": {"name": "Boston Red Sox"},
                    "probablePitcher": {"fullName": "Chris Sale"},
                },
            },
        }
    ]

    def fake_profile(name, season, era_medians, default_whip=1.3, default_ip=150.0):
        table = {
            "gerrit cole": {"era": 3.20, "fip": None, "whip": 1.05, "ip": 160.0},
            "chris sale": {"era": 4.85, "fip": None, "whip": 1.40, "ip": 120.0},
        }
        key = (name or "").lower().strip()
        if key in table:
            return table[key]
        return {
            "era": era_medians.get(season, era_medians["default"]),
            "fip": None,
            "whip": default_whip,
            "ip": default_ip,
        }

    empty_history = pd.DataFrame(
        columns=[
            "game_id",
            "date",
            "home_team",
            "away_team",
            "home_score",
            "away_score",
            "home_win",
            "season",
        ]
    )

    with (
        patch("app.parlay.slate.fetch_mlb_schedule_day", return_value=fake_games),
        patch("app.parlay.slate.load_games", return_value=empty_history),
        patch(
            "app.features.mlb_pregame.lookup_pitcher_profile",
            side_effect=fake_profile,
        ),
        patch(
            "app.parlay.slate.predict_home_win_proba",
            return_value=pd.Series([0.55]),
        ),
    ):
        from datetime import date

        slate = build_slate_dataframe(date(2026, 6, 15))

    assert len(slate) == 1
    assert slate.iloc[0]["home_pitcher_era"] == 3.20
    assert slate.iloc[0]["away_pitcher_era"] == 4.85
    assert slate.iloc[0]["home_pitcher_era"] != slate.iloc[0]["away_pitcher_era"]
