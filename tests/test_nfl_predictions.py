"""NFL slate predictions API tests."""

from unittest.mock import patch

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

MOCK_GAME = {
    "game_id": "401671617",
    "home_team": "Kansas City Chiefs",
    "away_team": "Baltimore Ravens",
    "home_team_abbr": "KC",
    "away_team_abbr": "BAL",
    "home_logo_url": "https://example.com/kc.png",
    "away_logo_url": "https://example.com/bal.png",
    "start_time_utc": "2024-09-05T00:20:00Z",
    "status": "Preview",
    "sport": "nfl",
    "game_type": "regular",
}


def _attach_none(df, _day, **kwargs):
    out = df.copy()
    out["home_ml"] = np.nan
    out["away_ml"] = np.nan
    out["home_spread_point"] = np.nan
    out["ou_line"] = np.nan
    return out, "none"


@patch("app.services.nfl_slate_predictions.attach_nfl_odds", side_effect=_attach_none)
@patch("app.services.nfl_slate_predictions.predict_home_win_proba")
@patch("app.services.nfl_slate_predictions.predict_spread_covers")
@patch("app.services.nfl_slate_predictions.enrich_totals_columns")
@patch("app.services.nfl_slate_predictions.get_nfl_schedule")
def test_predictions_include_spread_and_totals(
    mock_schedule, mock_enrich, mock_spread, mock_ml, _mock_odds
):
    mock_schedule.return_value = {
        "date": "2024-09-05",
        "resolved_date": "2024-09-05",
        "games": [MOCK_GAME],
    }
    mock_ml.return_value = [0.62]
    mock_spread.return_value = pd.DataFrame(
        [
            {
                "game_id": "401671617",
                "model_margin": 4.5,
                "model_prob_home_cover": 0.58,
                "model_prob_away_cover": 0.42,
            }
        ]
    )
    mock_enrich.return_value = pd.DataFrame(
        [
            {
                "game_id": "401671617",
                "expected_total_pts": 46.2,
                "model_prob_over": 0.56,
                "ou_line": 44.5,
            }
        ]
    )

    resp = client.get("/api/nfl/predictions", params={"date": "2024-09-05"})
    assert resp.status_code == 200
    data = resp.json()
    row = data["401671617"]
    assert row["model_prob_home"] == 0.62
    assert row["model_pick"] == "Kansas City Chiefs"
    assert row["model_pick_side"] == "home"
    assert row["game_id"] == "401671617"
    assert row["spread_pick"] is not None
    assert row["spread_line_source"] == "proxy"
    assert row["totals_pick"] == "Over 44.5"
    assert row["ou_line"] == 44.5


@patch("app.services.nfl_slate_predictions.predict_home_win_proba", side_effect=FileNotFoundError)
@patch("app.services.nfl_slate_predictions.get_nfl_schedule")
def test_predictions_empty_without_model(mock_schedule, _mock_ml):
    mock_schedule.return_value = {
        "date": "2024-09-05",
        "resolved_date": "2024-09-05",
        "games": [MOCK_GAME],
    }
    resp = client.get("/api/nfl/predictions", params={"date": "2024-09-05"})
    assert resp.status_code == 200
    assert resp.json() == {}
