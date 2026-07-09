"""Tests for UFC odds conversion and fuzzy matching."""

from pathlib import Path

import pandas as pd

from app.odds.ufc_odds_convert import convert_mikespa, detect_format
from app.odds.ufc_odds_match import merge_fights_odds_fuzzy

FIXTURE = Path(__file__).resolve().parents[1] / "data" / "fixtures" / "ufc_odds_mikespa_master.csv"


def test_detect_mikespa_format():
    if not FIXTURE.exists():
        return
    assert detect_format(FIXTURE) == "mikespa"


def test_convert_mikespa_has_rows():
    if not FIXTURE.exists():
        return
    df = convert_mikespa(pd.read_csv(FIXTURE))
    assert len(df) > 1000
    assert {"date", "home_team", "away_team", "home_ml", "away_ml"}.issubset(df.columns)


def test_fuzzy_merge_swapped_corners():
    fights = pd.DataFrame(
        [
            {
                "fight_id": "1",
                "date": "2024-01-13",
                "home_team": "Jim Miller",
                "away_team": "Gabriel Benitez",
            }
        ]
    )
    odds = pd.DataFrame(
        [
            {
                "date": "2024-01-13",
                "home_team": "Gabriel Benitez",
                "away_team": "Jim Miller",
                "home_ml": 110,
                "away_ml": -120,
            }
        ]
    )
    merged = merge_fights_odds_fuzzy(fights, odds)
    assert len(merged) == 1
    assert int(merged.iloc[0]["home_ml"]) == -120
    assert int(merged.iloc[0]["away_ml"]) == 110
