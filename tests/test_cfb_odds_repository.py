"""CFB odds repository — parse fixture JSON and median math."""

from app.odds.cfb_odds_repository import normalize_cfb_events


def _mock_event_with_spreads() -> list[dict]:
    return [
        {
            "home_team": "Georgia Bulldogs",
            "away_team": "Georgia Tech Yellow Jackets",
            "commence_time": "2024-11-30T17:00:00Z",
            "bookmakers": [
                {
                    "key": "draftkings",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Georgia Bulldogs", "price": -350},
                                {"name": "Georgia Tech Yellow Jackets", "price": 280},
                            ],
                        },
                        {
                            "key": "spreads",
                            "outcomes": [
                                {"name": "Georgia Bulldogs", "point": -10.5, "price": -110},
                                {"name": "Georgia Tech Yellow Jackets", "point": 10.5, "price": -110},
                            ],
                        },
                        {
                            "key": "totals",
                            "outcomes": [
                                {"name": "Over", "point": 55.5, "price": -110},
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
                                {"name": "Georgia Bulldogs", "price": -340},
                                {"name": "Georgia Tech Yellow Jackets", "price": 270},
                            ],
                        },
                        {
                            "key": "spreads",
                            "outcomes": [
                                {"name": "Georgia Bulldogs", "point": -11.0, "price": -108},
                                {"name": "Georgia Tech Yellow Jackets", "point": 11.0, "price": -112},
                            ],
                        },
                    ],
                },
            ],
        }
    ]


def test_normalize_cfb_events_parses_spread_medians():
    games = normalize_cfb_events(_mock_event_with_spreads())
    assert len(games) == 1
    g = games[0]
    assert g["home_team"] == "Georgia"
    assert g["away_team"] == "Georgia Tech"
    assert g["home_ml"] == -345
    assert g["home_spread_point"] == -10.75
    assert g["away_spread_point"] == 10.75
    assert g["ou_line"] == 55.5


def test_normalize_cfb_events_h2h_only():
    events = [
        {
            "home_team": "Alabama Crimson Tide",
            "away_team": "Auburn Tigers",
            "bookmakers": [
                {
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Alabama Crimson Tide", "price": -200},
                                {"name": "Auburn Tigers", "price": 170},
                            ],
                        }
                    ]
                }
            ],
        }
    ]
    games = normalize_cfb_events(events)
    assert len(games) == 1
    assert games[0]["home_team"] == "Alabama"
    assert games[0]["away_team"] == "Auburn"
    assert games[0]["home_spread_point"] is None
