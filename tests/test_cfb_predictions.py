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
    "division": "fbs",
    "divisions": ["fbs"],
    "model_eligible": True,
    "model_family": "cfb_moneyline",
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

    from app.services.cfb_slate_predictions import predict_slate

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

    data = predict_slate(date(2024, 11, 30))
    row = data["401635000"]
    assert row["model_prob_home"] == 0.72
    assert row["model_belief_home_pct"] == 72
    assert row["model_belief_away_pct"] == 28
    assert row["model_belief_home_pct"] + row["model_belief_away_pct"] == 100
    assert row["active_model_version"] == "v4_logistic_platt"
    assert row["active_feature_set"] == "cfb_v4"
    assert row["model_pick"] == "Georgia"
    assert row["model_pick_side"] == "home"
    assert row["model_category_label"] in ("Toss-up", "Soft", "Hard", "Lock")
    assert row["spread_pick"] is not None
    assert row["spread_line_source"] == "proxy"
    assert row["totals_pick"] == "Over 51.5"
    assert row["ou_line"] == 51.5
    assert row["ou_line_source"] == "book"
    assert "expected_total_pts" in row


@patch("app.main.predict_cfb_slate")
def test_cfb_predictions_api_does_not_use_ufc_predictor(mock_cfb):
    mock_cfb.return_value = {
        "401635000": {
            "game_id": "401635000",
            "home_team": "Georgia",
            "away_team": "Alabama",
            "model_prob_home": 0.61,
        }
    }
    resp = client.get("/api/cfb/predictions?date=2024-11-30")
    assert resp.status_code == 200
    body = resp.json()
    assert "401635000" in body
    assert body["401635000"]["home_team"] == "Georgia"
    assert "event_name" not in body["401635000"]
    mock_cfb.assert_called_once()


def test_model_team_name_strips_espn_mascot_for_history_match():
    from app.services.cfb_slate_predictions import _model_team_name
    canonical = ("North Carolina", "TCU", "San Jose State", "USC")
    assert _model_team_name({"home_team": "TCU Horned Frogs"}, "home", canonical) == "TCU"
    assert _model_team_name({"away_team": "North Carolina Tar Heels"}, "away", canonical) == "North Carolina"
    assert _model_team_name({"away_team": "San José State Spartans"}, "away", canonical) == "San Jose State"


def test_model_team_name_prefers_espn_location():
    from app.services.cfb_slate_predictions import _model_team_name
    canonical = ("North Dakota State",)
    game = {"home_team": "NDSU Bison", "home_team_model_name": "North Dakota State"}
    assert _model_team_name(game, "home", canonical) == "North Dakota State"

def test_fcs_beta_cap_winner_and_no_high_confidence(monkeypatch):
    import pandas as pd
    from app.features.fcs_pregame import FEATURE_COLUMNS
    from app.services.cfb_slate_predictions import _fcs_beta_predictions
    game={"game_id":"fcs-1","home_team":"Montana","away_team":"Weber State","division":"fcs","divisions":["fcs"],"neutral_site":0,"neutral_site_known":True,"neutral_site_missing":False}
    row=pd.Series({name:0.0 for name in FEATURE_COLUMNS})
    monkeypatch.setattr("app.services.cfb_slate_predictions.load_fcs_artifact",lambda:{"model_version":"fcs_v1_logistic_platt"})
    monkeypatch.setattr("app.services.cfb_slate_predictions.live_game_features",lambda *a:row)
    monkeypatch.setattr("app.services.cfb_slate_predictions.predict_fcs",lambda *a:[.96])
    monkeypatch.setattr("app.services.cfb_slate_predictions.fcs_diagnostic",lambda *a:[])
    pred=_fcs_beta_predictions({"games":[game]},"2026-08-29")["fcs-1"]
    assert pred["model_pick"]=="Montana"
    assert pred["raw_model_prob_home"]==.96 and pred["model_prob_home"]==.90
    assert pred["display_probability_capped"] is True
    assert pred["tier_validated"] is False
    assert pred["public_tier_label"] is None
    assert "Elite tier unvalidated" in pred["model_category_label"]
    assert "BAM" not in str(pred) and "High" not in str(pred)

def test_fcs_beta_fails_closed_for_mixed_or_unknown_site(monkeypatch):
    from app.services.cfb_slate_predictions import _fcs_beta_predictions
    monkeypatch.setattr("app.services.cfb_slate_predictions.load_fcs_artifact",lambda:{"model_version":"fcs_v1_logistic_platt"})
    mixed={"game_id":"mixed","division":"fbs","divisions":["fbs","fcs"],"neutral_site":0,"neutral_site_known":True}
    unknown={"game_id":"unknown","division":"fcs","divisions":["fcs"],"neutral_site_missing":True}
    assert _fcs_beta_predictions({"games":[mixed,unknown]},"2026-08-29")=={}


def test_fcs_diagnostic_routes_to_fcs_without_fbs_fallback(monkeypatch):
    game = {
        "game_id": "fcs-diagnostic",
        "home_team": "Montana",
        "away_team": "Weber State",
        "division": "fcs",
        "divisions": ["fcs"],
    }
    prediction = {
        "model_pick": "Montana",
        "model_pick_side": "home",
        "raw_model_prob_home": 0.93,
        "raw_model_prob_away": 0.07,
        "model_prob_home": 0.90,
        "model_prob_away": 0.10,
        "candidate_tier": "Elite",
        "tier_validated": False,
        "public_tier_label": None,
        "tier_status_label": "FCS Beta · Elite tier unvalidated",
        "tier_validation_reason": "insufficient evidence",
        "tier_policy_version": "test",
        "diagnostics": {"raw_calibrated_probability": 0.93, "features": []},
    }
    monkeypatch.setattr(
        "app.services.schedule_cfb.get_cfb_schedule",
        lambda _day: {"games": [game]},
    )
    monkeypatch.setattr(
        "app.services.cfb_slate_predictions._fcs_beta_predictions",
        lambda *_args: {"fcs-diagnostic": prediction},
    )

    with patch(
        "app.services.cfb_prediction_diagnostic.diagnose_cfb_prediction"
    ) as fbs_diagnostic:
        response = client.get(
            "/api/cfb/diagnostics/fcs-diagnostic?date=2026-08-29"
        )

    assert response.status_code == 200
    body = response.json()
    assert body["model_family"] == "fcs_moneyline"
    assert body["prediction"]["raw_model_prob_home"] == 0.93
    assert body["prediction"]["model_prob_home"] == 0.90
    assert body["prediction"]["public_tier_label"] is None
    fbs_diagnostic.assert_not_called()
