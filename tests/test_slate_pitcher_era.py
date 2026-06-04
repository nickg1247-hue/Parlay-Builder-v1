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

    def fake_lookup(name, season, era_medians):
        table = {
            "gerrit cole": (3.20, None),
            "chris sale": (4.85, None),
        }
        key = (name or "").lower().strip()
        if key in table:
            return table[key]
        return era_medians.get(season, era_medians["default"]), None

    empty_history = pd.DataFrame(
        columns=[
            "game_id",
            "date",
            "home_team",
            "away_team",
            "home_score",
            "away_score",
            "home_win",
        ]
    )

    with (
        patch("app.parlay.slate.fetch_mlb_schedule_day", return_value=fake_games),
        patch("app.parlay.slate.load_games", return_value=empty_history),
        patch("app.parlay.slate.lookup_pitcher_rates", side_effect=fake_lookup),
        patch(
            "app.parlay.slate.predict_home_win_proba",
            return_value=pd.Series([0.55]),
        ),
        patch(
            "app.parlay.slate.load_model_artifact",
            return_value={"era_medians": {2026: 4.0, "default": 4.0}, "rest_fill": 1},
        ),
    ):
        from datetime import date

        slate = build_slate_dataframe(date(2026, 6, 15))

    assert len(slate) == 1
    assert slate.iloc[0]["home_pitcher_era"] == 3.20
    assert slate.iloc[0]["away_pitcher_era"] == 4.85
    assert slate.iloc[0]["home_pitcher_era"] != slate.iloc[0]["away_pitcher_era"]
