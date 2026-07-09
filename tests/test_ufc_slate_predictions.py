"""Tests for UFC slate predictions API payload."""

from __future__ import annotations

import json
import math
from datetime import date
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.ufc_slate_predictions import predict_slate, _clean_json_value

client = TestClient(app)


def test_clean_json_value_strips_nan():
    out = _clean_json_value({"totals_line": float("nan"), "ok": 1})
    assert out["totals_line"] is None
    assert out["ok"] == 1


@patch("app.main.predict_slate")
def test_ufc_predictions_api_json_serializable(mock_preds):
    mock_preds.return_value = {
        "401867788": {
            "fight_id": "401867788",
            "game_id": "401867788",
            "model_prob_home": 0.55,
            "model_prob_away": 0.45,
            "totals_line": float("nan"),
            "over_odds": float("nan"),
        }
    }
    resp = client.get("/api/ufc/predictions?date=2026-07-11")
    assert resp.status_code == 200
    body = resp.json()
    preds = body.get("predictions", body)
    assert preds["401867788"]["model_prob_home"] == 0.55
    assert preds["401867788"]["totals_line"] is None
    assert "model_label" in body
    json.dumps(body)


def test_predict_slate_returns_probs_for_card():
    preds = predict_slate(date(2026, 7, 11))
    assert preds
    sample = next(iter(preds.values()))
    assert sample.get("model_prob_home") is not None
    assert 0.0 < float(sample["model_prob_home"]) < 1.0
    json.dumps(preds)
