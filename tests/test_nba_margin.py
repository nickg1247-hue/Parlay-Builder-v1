"""NBA margin model tests (mini fixtures — no full parquet required)."""

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from app.features import nba_pregame as nfp
from app.models import nba_margin as nm
from app.odds.spread_math import model_prob_home_cover


def _mini_games() -> pd.DataFrame:
    rows = []
    teams = [
        ("Boston Celtics", "New York Knicks"),
        ("Miami Heat", "Boston Celtics"),
        ("Los Angeles Lakers", "Golden State Warriors"),
    ]
    dates = ["2023-10-24", "2023-10-26", "2023-10-28"]
    scores = [(108, 104), (100, 110), (115, 112)]
    for i, ((home, away), dt, (hs, aws)) in enumerate(zip(teams, dates, scores)):
        rows.append(
            {
                "game_id": f"g{i+1}",
                "date": dt,
                "season": 2024,
                "game_type": "regular",
                "home_team": home,
                "away_team": away,
                "home_score": hs,
                "away_score": aws,
                "home_win": int(hs > aws),
                "home_rest_days": 2.0,
                "away_rest_days": 2.0,
                "home_b2b": 0,
                "away_b2b": 0,
            }
        )
    extra = []
    for gid, dt, season in [
        ("t1", "2024-01-05", 2024),
        ("t2", "2024-02-05", 2024),
        ("t3", "2025-01-05", 2025),
        ("t4", "2025-02-05", 2025),
        ("h1", "2026-01-05", 2026),
        ("h2", "2026-02-05", 2026),
    ]:
        row = rows[0].copy()
        hw = 0 if gid == "h2" else 1
        hs, aws = (98, 105) if hw == 0 else (105, 100)
        row.update(
            {
                "game_id": gid,
                "date": dt,
                "season": season,
                "home_score": hs,
                "away_score": aws,
                "home_win": hw,
            }
        )
        extra.append(row)
    return pd.DataFrame(rows + extra)


def test_margin_production_gate_logic():
    assert nm.margin_production_gate_passes(
        {
            "holdout_mae_margin": 12.0,
            "proxy_cover_log_loss_home": 0.65,
            "proxy_cover_log_loss_away": 0.66,
            "margin_derived_ml_log_loss": 0.60,
            "v2_logistic_log_loss": 0.6035,
        }
    )
    assert not nm.margin_production_gate_passes(
        {
            "holdout_mae_margin": 16.0,
            "proxy_cover_log_loss_home": 0.65,
            "proxy_cover_log_loss_away": 0.66,
            "margin_derived_ml_log_loss": 0.60,
            "v2_logistic_log_loss": 0.6035,
        }
    )


@patch("app.models.nba_margin.load_games")
def test_predict_home_win_proba_from_margin_range(mock_load, tmp_path, monkeypatch):
    games = _mini_games()
    mock_load.return_value = games.sort_values(["date", "game_id"]).reset_index(drop=True)
    monkeypatch.setattr(nm, "MODEL_ARTIFACT", tmp_path / "nba_margin_model.joblib")
    monkeypatch.setattr(nm, "METRICS_JSON", tmp_path / "nba_margin_metrics.json")
    monkeypatch.setattr(nm, "ACTIVE_MARGIN_MANIFEST", tmp_path / "active_nba_margin_model.json")

    nm.run_training()
    feat = nfp.build_features(games.head(3), rest_fill=2.0, pts_fill=110.0, attach_elo=True)
    probs = nm.predict_home_win_proba_from_margin(feat)
    assert len(probs) == 3
    assert np.all(probs > 0) and np.all(probs < 1)


@patch("app.models.nba_margin.load_games")
def test_predict_spread_covers_at_proxy_line(mock_load, tmp_path, monkeypatch):
    games = _mini_games()
    mock_load.return_value = games.sort_values(["date", "game_id"]).reset_index(drop=True)
    monkeypatch.setattr(nm, "MODEL_ARTIFACT", tmp_path / "nba_margin_model.joblib")
    monkeypatch.setattr(nm, "METRICS_JSON", tmp_path / "nba_margin_metrics.json")
    monkeypatch.setattr(nm, "ACTIVE_MARGIN_MANIFEST", tmp_path / "active_nba_margin_model.json")

    nm.run_training()
    artifact = nm.load_margin_artifact()
    feat = nfp.build_features(games.head(2), rest_fill=2.0, pts_fill=110.0, attach_elo=True)
    feat["home_spread_point"] = -5.5
    feat["away_spread_point"] = 5.5
    out = nm.predict_spread_covers(feat)
    assert "model_margin" in out.columns
    assert out["model_prob_home_cover"].notna().all()
    margin = float(out["model_margin"].iloc[0])
    std = float(artifact["margin_std"])
    expected = model_prob_home_cover(margin, std, -5.5)
    assert out["model_prob_home_cover"].iloc[0] == pytest.approx(expected, rel=1e-3)


@patch("app.models.nba_margin.load_games")
def test_run_training_writes_metrics(mock_load, tmp_path, monkeypatch):
    games = _mini_games()
    mock_load.return_value = games.sort_values(["date", "game_id"]).reset_index(drop=True)
    monkeypatch.setattr(nm, "MODEL_ARTIFACT", tmp_path / "nba_margin_model.joblib")
    monkeypatch.setattr(nm, "METRICS_JSON", tmp_path / "nba_margin_metrics.json")
    monkeypatch.setattr(nm, "ACTIVE_MARGIN_MANIFEST", tmp_path / "active_nba_margin_model.json")

    results = nm.run_training()
    assert "holdout_mae_margin" in results
    assert "margin_derived_ml_log_loss" in results
    assert "v2_logistic_log_loss" in results
    assert tmp_path.joinpath("nba_margin_model.joblib").exists()
