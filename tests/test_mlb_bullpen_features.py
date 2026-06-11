"""Team bullpen rolling features — 14d ERA, 3d IP, no future leakage."""

import pandas as pd
import pytest

from app.data.pitcher_form import (
    team_bullpen_era_14d,
    team_bullpen_features,
    team_bullpen_ip_3d,
)


def _bullpen_log() -> pd.DataFrame:
    rows = [
        # Outside 14d window (too old)
        {
            "game_id": "0",
            "date": pd.Timestamp("2024-04-01"),
            "season": 2024,
            "team": "TeamA",
            "pitcher_name": "Relief Old",
            "pitcher_key": "relief old",
            "ip": 1.0,
            "er": 0,
            "hits": 0,
            "walks": 0,
            "is_starter": False,
        },
        # In 14d window
        {
            "game_id": "1",
            "date": pd.Timestamp("2024-04-10"),
            "season": 2024,
            "team": "TeamA",
            "pitcher_name": "Relief A",
            "pitcher_key": "relief a",
            "ip": 2.0,
            "er": 1,
            "hits": 2,
            "walks": 1,
            "is_starter": False,
        },
        {
            "game_id": "2",
            "date": pd.Timestamp("2024-04-12"),
            "season": 2024,
            "team": "TeamA",
            "pitcher_name": "Relief B",
            "pitcher_key": "relief b",
            "ip": 1.0,
            "er": 0,
            "hits": 1,
            "walks": 0,
            "is_starter": False,
        },
        # In 3d window only (Apr 20–22)
        {
            "game_id": "3",
            "date": pd.Timestamp("2024-04-21"),
            "season": 2024,
            "team": "TeamA",
            "pitcher_name": "Relief C",
            "pitcher_key": "relief c",
            "ip": 1.5,
            "er": 2,
            "hits": 3,
            "walks": 1,
            "is_starter": False,
        },
        # Starter — must be excluded
        {
            "game_id": "4",
            "date": pd.Timestamp("2024-04-21"),
            "season": 2024,
            "team": "TeamA",
            "pitcher_name": "Starter",
            "pitcher_key": "starter",
            "ip": 6.0,
            "er": 3,
            "hits": 5,
            "walks": 2,
            "is_starter": True,
        },
        # Same day as eval — must be excluded
        {
            "game_id": "5",
            "date": pd.Timestamp("2024-04-23"),
            "season": 2024,
            "team": "TeamA",
            "pitcher_name": "Relief Future",
            "pitcher_key": "relief future",
            "ip": 2.0,
            "er": 0,
            "hits": 0,
            "walks": 0,
            "is_starter": False,
        },
    ]
    return pd.DataFrame(rows)


def test_bullpen_era_14d_rolls_relief_only():
    log = _bullpen_log()
    before = pd.Timestamp("2024-04-23")
    era = team_bullpen_era_14d("TeamA", before, log)
    # Apr 10 + Apr 12 + Apr 21 relief: ER=1+0+2=3, IP=4.5 → ERA=6.0
    assert era == pytest.approx(6.0, rel=1e-4)


def test_bullpen_ip_3d_sums_prior_days():
    log = _bullpen_log()
    before = pd.Timestamp("2024-04-23")
    ip3 = team_bullpen_ip_3d("TeamA", before, log)
    # Apr 21 relief only (Apr 23 excluded; Apr 20 has no rows)
    assert ip3 == pytest.approx(1.5, rel=1e-4)


def test_bullpen_no_future_games_in_window():
    log = _bullpen_log()
    before = pd.Timestamp("2024-04-22")
    ip3 = team_bullpen_ip_3d("TeamA", before, log)
    assert ip3 == pytest.approx(1.5, rel=1e-4)


def test_bullpen_features_use_computed_or_fallback():
    log = _bullpen_log()
    before = pd.Timestamp("2024-04-23")
    feats = team_bullpen_features("TeamA", before, log)
    assert feats["era_14d"] == pytest.approx(6.0, rel=1e-4)
    assert feats["ip_3d"] == pytest.approx(1.5, rel=1e-4)

    empty_feats = team_bullpen_features("TeamZ", before, log)
    assert empty_feats["era_14d"] > 0
    assert empty_feats["ip_3d"] >= 0
