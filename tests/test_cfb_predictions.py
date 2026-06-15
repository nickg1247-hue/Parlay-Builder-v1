"""CFB slate predictions API tests."""

from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

MOCK_GAME = {
    "game_id": "401635000",
    "home_team": "Georgia",
    "away_team": "Georgia Tech",
    "home_logo_url": "https://example.com/uga.png",
    "away_logo_url": "https://example.com/gt.png",
    "start_time_utc": "2024-11-30T20:00:00Z",
    "status": "Preview",
    "sport": "cfb",
}


@patch("app.services.cfb_slate_predictions.resolve_lines_for_slate")
@patch("app.services.cfb_slate_predictions.attach_cfb_odds")
@patch("app.services.cfb_slate_predictions.predict_home_win_proba")
@patch("app.services.cfb_slate_predictions.predict_spread_covers")
@patch("app.services.cfb_slate_predictions.enrich_totals_columns")
@patch("app.services.cfb_slate_predictions.get_cfb_schedule")
def test_predictions_include_spread_and_totals(
    mock_schedule,
    mock_enrich,
    mock_spread,
    mock_ml,
    mock_attach_odds,
    mock_lines,
):
    import pandas as pd
    import numpy as np

    mock_schedule.return_value = {
        "date": "2024-11-30",
        "resolved_date": "2024-11-30",
        "games": [MOCK_GAME],
    }

    def _attach(df, _day, **kwargs):
        out = df.copy()
        out["home_ml"] = np.nan
        out["away_ml"] = np.nan
        return out, "none"

    mock_attach_odds.side_effect = _attach
    mock_lines.return_value = ({"401635000": 51.5}, {}, {"401635000": 51.5})
    mock_ml.return_value = [0.72]
    mock_spread.return_value = pd.DataFrame(
        [
            {
                "game_id": "401635000",
                "model_margin": 10.5,
                "model_prob_home_cover": 0.62,
                "model_prob_away_cover": 0.38,
            }
        ]
    )
    mock_enrich.return_value = pd.DataFrame(
        [
            {
                "game_id": "401635000",
                "expected_total_pts": 54.2,
                "model_prob_over": 0.58,
                "ou_line": 51.5,
            }
        ]
    )

    resp = client.get("/api/cfb/predictions", params={"date": "2024-11-30"})
    assert resp.status_code == 200
    data = resp.json()
    row = data["401635000"] if isinstance(data, dict) else data[0]
    assert row["model_prob_home"] == 0.72
    assert row["model_pick"] == "Georgia"
    assert row["model_pick_side"] == "home"
    assert row["spread_pick"] is not None
    assert row["spread_line_source"] == "proxy"
    assert row["totals_pick"] == "Over 51.5"
    assert row["ou_line"] == 51.5
    assert row["ou_line_source"] == "book"
    assert "expected_total_pts" in row
