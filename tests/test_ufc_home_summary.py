"""UFC home chip tests."""

from unittest.mock import patch

import app.services.ufc_home_summary as uhs
from app.services.ufc_home_summary import get_ufc_home_chip


def _clear_cache():
    uhs._CHIP_CACHE = None
    uhs._CHIP_CACHE_AT = 0.0


def test_ufc_home_chip_no_card():
    _clear_cache()
    with patch("app.services.ufc_home_summary.get_ufc_schedule") as mock_sched:
        mock_sched.return_value = {"games": [], "resolved_date": "2024-01-01"}
        chip = get_ufc_home_chip()
    assert chip["available"] is False
    assert chip["main_event"] is None
    assert chip["best_ev_pick"] is None


def test_ufc_home_chip_with_fights():
    _clear_cache()
    with patch("app.services.ufc_home_summary.get_ufc_schedule") as mock_sched:
        mock_sched.return_value = {
            "games": [
                {
                    "fight_id": "401",
                    "home_team": "Fighter A",
                    "away_team": "Fighter B",
                    "event_name": "UFC 300",
                    "card_segment": "main",
                }
            ],
            "resolved_date": "2024-04-13",
            "days_ahead": 3,
        }
        with patch("app.services.ufc_home_summary.predict_slate") as mock_pred:
            mock_pred.return_value = {
                "401": {
                    "fight_id": "401",
                    "home_team": "Fighter A",
                    "away_team": "Fighter B",
                    "ev_home": 0.08,
                    "ev_away": 0.02,
                    "home_ml": -150,
                    "plus_ev_ml": True,
                }
            }
            chip = get_ufc_home_chip()
    assert chip["available"] is True
    assert chip["fight_count"] == 1
    assert chip["card_date"] == "2024-04-13"
    assert chip["headline_fight"] == "Fighter A vs Fighter B"
    assert chip["main_event"]["matchup"] == "Fighter A vs Fighter B"
    assert chip["href"] == "/ufc?date=2024-04-13"
    assert chip["best_ev_pick"]["fighter"] == "Fighter A"
    assert chip["plus_ev_count"] == 1
