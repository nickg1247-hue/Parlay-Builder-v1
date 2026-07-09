"""Tests for UFC fight stats rolling features — no same-bout leakage."""

from __future__ import annotations

import pandas as pd

from app.features.ufc_pregame import (
    STAT_ROLLING_N,
    build_features,
    build_fighter_stats_tracker_from_history,
)


def _sample_fights() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "fight_id": "f1",
                "event_id": "e1",
                "event_name": "Test 1",
                "date": "2024-01-01",
                "season": 2024,
                "home_team": "Fighter A",
                "away_team": "Fighter B",
                "home_win": 1,
                "weight_class": "Lightweight",
                "card_segment": "main",
                "home_rest_days": 90.0,
                "away_rest_days": 90.0,
                "home_b2b": 0,
                "away_b2b": 0,
            },
            {
                "fight_id": "f2",
                "event_id": "e1",
                "event_name": "Test 1",
                "date": "2024-02-01",
                "season": 2024,
                "home_team": "Fighter A",
                "away_team": "Fighter C",
                "home_win": 0,
                "weight_class": "Lightweight",
                "card_segment": "main",
                "home_rest_days": 31.0,
                "away_rest_days": 60.0,
                "home_b2b": 0,
                "away_b2b": 0,
            },
        ]
    )


def _sample_stats() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "fight_id": "f1",
                "event_id": "e1",
                "date": "2024-01-01",
                "season": 2024,
                "home_team": "Fighter A",
                "away_team": "Fighter B",
                "home_sig_strikes_landed": 80.0,
                "away_sig_strikes_landed": 40.0,
                "home_takedowns_landed": 2.0,
                "away_takedowns_landed": 0.0,
                "home_control_seconds": 120.0,
                "away_control_seconds": 30.0,
            },
            {
                "fight_id": "f2",
                "event_id": "e1",
                "date": "2024-02-01",
                "season": 2024,
                "home_team": "Fighter A",
                "away_team": "Fighter C",
                "home_sig_strikes_landed": 10.0,
                "away_sig_strikes_landed": 90.0,
                "home_takedowns_landed": 0.0,
                "away_takedowns_landed": 3.0,
                "home_control_seconds": 15.0,
                "away_control_seconds": 200.0,
            },
        ]
    )


def test_rolling_stats_exclude_current_fight():
    fights = _sample_fights()
    stats = _sample_stats()
    feat = build_features(fights, fight_stats_df=stats, attach_elo=False)
    row_f2 = feat[feat["fight_id"] == "f2"].iloc[0]
    assert row_f2["home_sig_strikes_landed_avg"] == 80.0
    assert row_f2["stats_available"] == 1
    assert row_f2["home_sig_strikes_landed_avg"] != 10.0


def test_first_fight_has_no_prior_stats():
    fights = _sample_fights().iloc[:1]
    stats = _sample_stats().iloc[:1]
    feat = build_features(fights, fight_stats_df=stats, attach_elo=False)
    row = feat.iloc[0]
    assert row["home_sig_strikes_landed_avg"] is None
    assert row["stats_available"] == 0


def test_stats_tracker_samples_before_is_strict():
    stats = _sample_stats()
    tracker = build_fighter_stats_tracker_from_history(stats)
    before_second = pd.Timestamp("2024-02-01")
    samples = tracker.samples_before("Fighter A", before_second)
    assert len(samples) == 1
    assert samples[0].sig_strikes_landed == 80.0
    assert len(samples) <= STAT_ROLLING_N
