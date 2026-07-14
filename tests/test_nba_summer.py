"""NBA Summer League integrated into the main NBA tab."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

from app.main import app
from app.services import scores_nba_summer as sns
from app.services.nba_summer_calibration import apply_summer_calibration, shrink_home_prob

client = TestClient(app)

SAMPLE_SUMMER_GAME = {
    "sport": "nba",
    "game_id": "401881859",
    "home_team": "Dallas Mavericks",
    "away_team": "Boston Celtics",
    "start_time_utc": "2026-07-14T19:00:00Z",
    "status": "Preview",
    "is_summer": True,
    "league_tag": "summer",
    "summer_league": "nba-summer-las-vegas",
    "series_summary": "Las Vegas Summer League",
}


def test_summer_leagues_default():
    leagues = sns.summer_leagues()
    assert "nba-summer-las-vegas" in leagues


def test_shrink_home_prob_pulls_toward_half():
    assert abs(shrink_home_prob(0.7) - 0.61) < 0.02  # default shrink 0.55
    assert shrink_home_prob(0.5) == 0.5


def test_apply_summer_calibration_ml_and_totals():
    df = pd.DataFrame(
        [
            {
                "game_id": "1",
                "is_summer": True,
                "model_prob_home": 0.8,
                "model_prob_away": 0.2,
                "model_margin": 10.0,
                "expected_total_pts": 200.0,
                "ou_line": 210.0,
            },
            {
                "game_id": "2",
                "is_summer": False,
                "model_prob_home": 0.8,
                "model_prob_away": 0.2,
                "model_margin": 10.0,
                "expected_total_pts": 200.0,
                "ou_line": 210.0,
            },
        ]
    )
    out = apply_summer_calibration(df)
    assert out.loc[0, "model_prob_home"] < 0.8
    assert abs(out.loc[1, "model_prob_home"] - 0.8) < 1e-9
    assert out.loc[0, "model_margin"] < 10.0
    assert out.loc[0, "expected_total_pts"] > 200.0


@patch("app.services.schedule_nba.fetch_nba_scores_day", return_value=[])
@patch("app.services.scores_nba_summer.summer_games_for_nba_tab")
def test_nba_schedule_includes_summer(mock_summer, _mock_nba):
    mock_summer.return_value = [SAMPLE_SUMMER_GAME]
    # Force live refresh path
    with patch("app.services.schedule_nba.cache_is_fresh", return_value=False):
        resp = client.get("/api/schedule/nba?date=2026-07-14")
    assert resp.status_code == 200
    body = resp.json()
    assert body["sport"] == "nba"
    assert body["games_count"] >= 1
    summer = [g for g in body["games"] if g.get("is_summer")]
    assert len(summer) == 1
    assert summer[0]["series_summary"]


@patch("app.services.schedule_nba.fetch_nba_scores_day", return_value=[])
@patch("app.services.scores_nba_summer.summer_games_for_nba_tab")
def test_legacy_summer_schedule_filters(mock_summer, _mock_nba):
    mock_summer.return_value = [SAMPLE_SUMMER_GAME]
    with patch("app.services.schedule_nba.cache_is_fresh", return_value=False):
        resp = client.get("/api/schedule/nba-summer?date=2026-07-14")
    assert resp.status_code == 200
    body = resp.json()
    assert body["games_count"] == 1
    assert body["games"][0]["is_summer"] is True


def test_nba_summer_pages_redirect_to_nba():
    r1 = client.get("/nba-summer", follow_redirects=False)
    assert r1.status_code in (301, 302)
    assert r1.headers["location"] == "/nba"

    r2 = client.get("/nba-summer/board", follow_redirects=False)
    assert r2.status_code in (301, 302)
    assert r2.headers["location"] == "/nba/board"

    r3 = client.get("/nba-summer/game/401881859?date=2026-07-14", follow_redirects=False)
    assert r3.status_code in (301, 302)
    assert "/nba/game/401881859" in r3.headers["location"]


def test_nba_pages_still_exist():
    assert client.get("/nba").status_code == 200
    assert client.get("/nba/board").status_code == 200
