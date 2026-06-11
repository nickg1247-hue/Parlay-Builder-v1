"""CFB walk-forward backtest tests."""

from unittest.mock import patch

import pandas as pd

from app.services.cfb_backtest_report import run_cfb_walk_forward_backtest
from tests.test_cfb_margin import _tiny_games_df


def test_walk_forward_backtest_smoke(tmp_path, monkeypatch):
    df = _tiny_games_df()
    path = tmp_path / "cfb_games.parquet"
    df.to_parquet(path, index=False)
    out = tmp_path / "cfb_backtest_report.json"

    monkeypatch.setattr("app.models.cfb_baseline.PARQUET_PATH", path)
    monkeypatch.setattr("app.services.cfb_backtest_report.REPORT_JSON", out)

    report = run_cfb_walk_forward_backtest(write_cache=True)

    assert report["status"] == "ok"
    assert len(report["folds"]) >= 2
    assert report["aggregate"]["holdout_games_scored"] > 0
    assert "feature_effects" in report
    assert report["feature_effects"]["logistic_importance_avg"]
    assert out.exists()


def test_backtest_missing_data(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.services.cfb_backtest_report.load_games",
        lambda: (_ for _ in ()).throw(FileNotFoundError("no data")),
    )
    monkeypatch.setattr(
        "app.services.cfb_backtest_report.REPORT_JSON",
        tmp_path / "report.json",
    )
    report = run_cfb_walk_forward_backtest(write_cache=False)
    assert report["status"] == "error"
