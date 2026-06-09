"""NBA odds repository — spread parsing tests."""

from app.odds.nba_odds_repository import normalize_nba_events


def _mock_event_with_spreads() -> list[dict]:
    return [
        {
            "home_team": "Boston Celtics",
            "away_team": "New York Knicks",
            "commence_time": "2026-04-10T23:00:00Z",
            "bookmakers": [
                {
                    "key": "draftkings",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Boston Celtics", "price": -150},
                                {"name": "New York Knicks", "price": 130},
                            ],
                        },
                        {
                            "key": "spreads",
                            "outcomes": [
                                {"name": "Boston Celtics", "point": -5.5, "price": -110},
                                {"name": "New York Knicks", "point": 5.5, "price": -110},
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
                                {"name": "Boston Celtics", "price": -145},
                                {"name": "New York Knicks", "price": 125},
                            ],
                        },
                        {
                            "key": "spreads",
                            "outcomes": [
                                {"name": "Boston Celtics", "point": -6.0, "price": -108},
                                {"name": "New York Knicks", "point": 6.0, "price": -112},
                            ],
                        },
                    ],
                },
            ],
        }
    ]


def test_normalize_nba_events_parses_spread_medians():
    games = normalize_nba_events(_mock_event_with_spreads())
    assert len(games) == 1
    g = games[0]
    assert g["home_ml"] == -147  # median of -150, -145
    assert g["home_spread_point"] == -5.75  # median of -5.5, -6.0
    assert g["away_spread_point"] == 5.75
    assert g["home_spread_american"] == -109
    assert g["away_spread_american"] == -111


def test_normalize_h2h_only_backward_compatible():
    events = [
        {
            "home_team": "Boston Celtics",
            "away_team": "New York Knicks",
            "bookmakers": [
                {
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Boston Celtics", "price": -150},
                                {"name": "New York Knicks", "price": 130},
                            ],
                        }
                    ]
                }
            ],
        }
    ]
    games = normalize_nba_events(events)
    assert len(games) == 1
    assert games[0]["home_spread_point"] is None
    assert games[0]["home_ml"] == -150
