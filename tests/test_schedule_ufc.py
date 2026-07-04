"""UFC schedule and slate resolution tests."""

from datetime import date
from unittest.mock import patch

from app.services.schedule_ufc import get_ufc_schedule, resolve_ufc_slate_date


def test_resolve_ufc_slate_date_finds_card():
    with patch("app.services.schedule_ufc._date_has_fights") as mock_has:
        mock_has.side_effect = lambda d: d == date(2024, 1, 13)
        resolved, offset = resolve_ufc_slate_date(date(2024, 1, 10))
        assert resolved == date(2024, 1, 13)
        assert offset == 3


def test_get_ufc_schedule_from_scoreboard():
    fake_events = [
        {
            "id": "600039893",
            "name": "UFC Fight Night: Test",
            "date": "2024-01-13T21:00Z",
            "competitions": [
                {
                    "id": "401623977",
                    "date": "2024-01-13T22:00Z",
                    "type": {"text": "Flyweight"},
                    "status": {"type": {"state": "pre"}},
                    "competitors": [
                        {
                            "order": 1,
                            "winner": False,
                            "athlete": {"displayName": "Fighter A"},
                        },
                        {
                            "order": 2,
                            "winner": False,
                            "athlete": {"displayName": "Fighter B"},
                        },
                    ],
                }
            ],
        }
    ]
    with patch("app.services.schedule_ufc._load_schedule_payload") as mock_load:
        mock_load.return_value = {
            "date": "2024-01-13",
            "sport": "ufc",
            "games": [
                {
                    "sport": "ufc",
                    "game_id": "401623977",
                    "fight_id": "401623977",
                    "event_id": "600039893",
                    "event_name": "UFC Fight Night: Test",
                    "home_team": "Fighter A",
                    "away_team": "Fighter B",
                    "weight_class": "Flyweight",
                    "status": "Preview",
                }
            ],
            "games_count": 1,
            "source": "api",
        }
        payload = get_ufc_schedule(date(2024, 1, 13), auto_resolve=False)
        assert payload["games_count"] == 1
        assert payload["games"][0]["home_team"] == "Fighter A"
