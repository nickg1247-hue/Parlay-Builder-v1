"""Tests for UFC odds attach (live + historical holdout)."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pandas as pd

from app.services.ufc_odds_attach import attach_ufc_odds


@patch("app.services.ufc_odds_attach.get_ufc_odds_for_date", return_value=(None, "none"))
@patch("app.services.ufc_odds_attach.load_holdout_odds")
def test_attach_ufc_odds_uses_holdout_for_past_dates(mock_holdout, _mock_live):
    mock_holdout.return_value = pd.DataFrame(
        [
            {
                "date": "2024-01-13",
                "home_team": "Magomed Ankalaev",
                "away_team": "Johnny Walker",
                "home_ml": -550,
                "away_ml": 410,
                "odds_source": "csv",
            }
        ]
    )
    slate = pd.DataFrame(
        [
            {
                "fight_id": "1",
                "home_team": "Magomed Ankalaev",
                "away_team": "Johnny Walker",
            }
        ]
    )
    merged, source = attach_ufc_odds(slate, date(2024, 1, 13))
    assert int(merged.iloc[0]["home_ml"]) == -550
    assert int(merged.iloc[0]["away_ml"]) == 410
    assert source == "csv"
