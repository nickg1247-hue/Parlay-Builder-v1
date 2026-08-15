"""NFL odds repository — parse fixture JSON and team aliases."""

from app.odds.nfl_odds_repository import normalize_nfl_events
from app.odds.nfl_team_aliases import normalize_nfl_team


def _mock_event_with_spreads() -> list[dict]:
    return [
        {
            "home_team": "Kansas City Chiefs",
            "away_team": "Baltimore Ravens",
            "commence_time": "2025-09-07T17:00:00Z",
            "bookmakers": [
                {
                    "key": "draftkings",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Kansas City Chiefs", "price": -140},
                                {"name": "Baltimore Ravens", "price": 120},
                            ],
                        },
                        {
                            "key": "spreads",
                            "outcomes": [
                                {"name": "Kansas City Chiefs", "point": -3.0, "price": -110},
                                {"name": "Baltimore Ravens", "point": 3.0, "price": -110},
                            ],
                        },
                        {
                            "key": "totals",
                            "outcomes": [
                                {"name": "Over", "point": 47.5, "price": -110},
                                {"name": "Under", "price": -110},
                            ],
                        },
                    ],
                },
                {
                    "key": "fanduel",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Kansas City Chiefs", "price": -135},
                                {"name": "Baltimore Ravens", "price": 115},
                            ],
                        },
                        {
                            "key": "spreads",
                            "outcomes": [
                                {"name": "Kansas City Chiefs", "point": -2.5, "price": -108},
                                {"name": "Baltimore Ravens", "point": 2.5, "price": -112},
                            ],
                        },
                    ],
                },
            ],
        }
    ]


def test_normalize_nfl_team_full_name_and_alias():
    assert normalize_nfl_team("Kansas City Chiefs") == "KC"
    assert normalize_nfl_team("Las Vegas Raiders") == "LV"
    assert normalize_nfl_team("Oakland Raiders") == "LV"
    assert normalize_nfl_team("WAS") == "WSH"
    assert normalize_nfl_team("Washington Commanders") == "WSH"


def test_normalize_nfl_events_parses_spread_medians():
    games = normalize_nfl_events(_mock_event_with_spreads())
    assert len(games) == 1
    g = games[0]
    assert g["home_team"] == "KC"
    assert g["away_team"] == "BAL"
    assert g["home_ml"] == -137
    assert g["home_spread_point"] == -2.75
    assert g["away_spread_point"] == 2.75
    assert g["ou_line"] == 47.5
