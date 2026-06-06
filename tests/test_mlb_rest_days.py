"""Regression tests for live-slate rest-day imputation."""

import pandas as pd

from app.features.mlb_pregame import (
    MAX_REST_GAP_DAYS,
    _GameRecord,
    _compute_rest_days,
    build_features_for_slate,
)


def _record(game_date: str, season: int) -> _GameRecord:
    ts = pd.Timestamp(game_date)
    return _GameRecord(ts, "TeamA", 1, 1, True, season)


def test_compute_rest_days_none_prior_uses_rest_fill():
    assert (
        _compute_rest_days([], pd.Timestamp("2026-06-15"), 1.0, 2026)
        == 1.0
    )


def test_compute_rest_days_gap_over_14_uses_rest_fill():
    prior = [_record("2025-09-28", 2025)]
    game_date = pd.Timestamp("2026-06-15")
    assert _compute_rest_days(prior, game_date, 1.0, 2026) == 1.0


def test_compute_rest_days_no_current_season_games_uses_rest_fill():
    prior = [_record("2025-09-20", 2025), _record("2025-09-28", 2025)]
    game_date = pd.Timestamp("2026-04-01")
    assert _compute_rest_days(prior, game_date, 2.5, 2026) == 2.5


def test_compute_rest_days_same_season_short_gap():
    prior = [_record("2026-06-10", 2026)]
    game_date = pd.Timestamp("2026-06-15")
    assert _compute_rest_days(prior, game_date, 1.0, 2026) == 5.0


def test_compute_rest_days_at_max_gap_boundary():
    prior = [_record("2026-06-01", 2026)]
    game_date = pd.Timestamp("2026-06-15")
    gap = (game_date - prior[0].date).days
    assert gap == MAX_REST_GAP_DAYS
    assert _compute_rest_days(prior, game_date, 1.0, 2026) == float(gap)


def test_slate_stale_history_caps_rest_days():
    history = pd.DataFrame(
        [
            {
                "game_id": "h1",
                "date": "2025-09-28",
                "home_team": "New York Yankees",
                "away_team": "Boston Red Sox",
                "home_score": 3,
                "away_score": 2,
                "home_win": 1,
                "season": 2025,
            }
        ]
    )
    slate_rows = pd.DataFrame(
        [
            {
                "game_id": "live1",
                "date": "2026-06-15",
                "home_team": "New York Yankees",
                "away_team": "Boston Red Sox",
                "season": 2026,
            }
        ]
    )
    feats = build_features_for_slate(
        slate_rows, history_df=history, era_medians={"default": 4.0}, rest_fill=1.0
    )
    row = feats.iloc[0]
    assert row["home_rest_days"] == 1.0
    assert row["away_rest_days"] == 1.0
    assert row["home_rest_days"] <= MAX_REST_GAP_DAYS
    assert row["away_rest_days"] <= MAX_REST_GAP_DAYS
