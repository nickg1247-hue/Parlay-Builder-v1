"""CFB prediction diagnostic tests."""
from __future__ import annotations
import numpy as np
import pandas as pd
from app.services.cfb_prediction_diagnostic import diagnose_cfb_prediction

class _Scaler:
    def transform(self, values): return values
class _Classifier:
    coef_ = np.array([[2.0]])
    intercept_ = np.array([0.0])
class _Pipe:
    named_steps = {"scaler": _Scaler(), "clf": _Classifier()}
    def predict_proba(self, values):
        p = 1.0 / (1.0 + np.exp(-2.0 * values[:, 0]))
        return np.column_stack([1-p, p])

def test_diagnostic_rejoins_game_and_explains_contribution(monkeypatch):
    history = pd.DataFrame([{"game_id":"g","date":"2025-09-01","season":2025,
        "home_team":"Home","away_team":"Away","home_score":24,"away_score":17,"home_win":1}])
    prepared = pd.DataFrame([{"game_id":"g","elo_diff":1.0,
        "elo_home_pre":1501.0,"elo_away_pre":1500.0}])
    monkeypatch.setattr("app.services.cfb_prediction_diagnostic.load_games", lambda: history)
    monkeypatch.setattr("app.services.cfb_prediction_diagnostic.load_model_artifact",
        lambda: {"feature_columns":["elo_diff"],"model":_Pipe(),"model_version":"test","feature_set":"test"})
    monkeypatch.setattr("app.services.cfb_prediction_diagnostic.build_features_for_slate",
        lambda *args, **kwargs: prepared)
    out = diagnose_cfb_prediction("g")
    assert out["game"]["actual_result"]["winner"] == "Home"
    assert out["prediction"]["predicted_winner"] == "Home"
    assert out["features"][0]["home_logit_contribution"] == 2.0
    assert out["features"][0]["home_raw"] == 1501.0
    assert out["features"][0]["away_raw"] == 1500.0
    assert "source_freshness" in out["features"][0]
    assert "neutral_site" in out["data_quality"]["missing_required_dataset_columns"]
