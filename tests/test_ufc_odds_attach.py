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


@patch("app.services.ufc_odds_attach.get_ufc_odds_for_date")
def test_attach_ufc_odds_method_and_round_props(mock_live):
    mock_live.return_value = (
        [
            {
                "home_team": "Magomed Ankalaev",
                "away_team": "Johnny Walker",
                "home_ml": -550,
                "away_ml": 410,
                "totals_line": 2.5,
                "over_odds": -115,
                "under_odds": -105,
                "method_props": {
                    "fighterA_KO_TKO": 450,
                    "fighterB_Decision": 180,
                },
                "goes_distance_yes": 150,
                "goes_distance_no": -175,
            }
        ],
        "the_odds_api_live",
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
    merged, source = attach_ufc_odds(slate, date(2026, 7, 11))
    row = merged.iloc[0]
    assert row["method_props"]["fighterA_KO_TKO"] == 450
    assert float(row["totals_line"]) == 2.5
    assert int(row["goes_distance_yes"]) == 150
    assert source == "the_odds_api_live"
