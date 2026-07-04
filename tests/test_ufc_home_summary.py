"""UFC home chip tests."""

from unittest.mock import patch

from app.services.ufc_home_summary import get_ufc_home_chip


def test_ufc_home_chip_no_card():
    with patch("app.services.ufc_home_summary.get_ufc_schedule") as mock_sched:
        mock_sched.return_value = {"games": [], "resolved_date": "2024-01-01"}
        chip = get_ufc_home_chip()
    assert chip["available"] is False


def test_ufc_home_chip_with_fights():
    with patch("app.services.ufc_home_summary.get_ufc_schedule") as mock_sched:
        mock_sched.return_value = {
            "games": [
                {
                    "fight_id": "1",
                    "home_team": "Fighter A",
                    "away_team": "Fighter B",
                    "event_name": "UFC 300",
                }
            ],
            "resolved_date": "2024-04-13",
            "days_ahead": 3,
        }
        with patch("app.services.ufc_home_summary.predict_slate") as mock_pred:
            mock_pred.return_value = {}
            chip = get_ufc_home_chip()
    assert chip["available"] is True
    assert chip["fight_count"] == 1
    assert "Fighter A" in chip["headline_fight"]
