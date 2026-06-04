"""Totals feature build should stay fast for daily slate scoring."""

import time
from datetime import date

import pandas as pd

from app.data.mlb_games import load_games_with_totals
from app.features.mlb_totals_pregame import (
    build_totals_features_for_slate,
    get_runs_tracker_before,
)


def test_tracker_precompute_under_one_second():
    games = load_games_with_totals()
    cutoff = pd.Timestamp("2025-08-15")
    start = time.perf_counter()
    tracker = get_runs_tracker_before(cutoff)
    elapsed = time.perf_counter() - start
    assert len(tracker._team_records) > 0
    assert elapsed < 1.0


def test_slate_features_under_two_seconds():
    games = load_games_with_totals()
    day = games[games["date"].dt.date == date(2025, 8, 15)].head(15)
    if day.empty:
        day = games[games["season"] == 2025].head(15)
    slate = day[
        ["game_id", "date", "home_team", "away_team", "season"]
    ].copy()
    start = time.perf_counter()
    feats = build_totals_features_for_slate(slate)
    elapsed = time.perf_counter() - start
    assert len(feats) == len(slate)
    assert elapsed < 2.0
