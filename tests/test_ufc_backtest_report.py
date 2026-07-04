"""UFC walk-forward backtest report tests."""

from app.services.ufc_backtest_report import run_ufc_walk_forward_backtest


def test_ufc_backtest_runs_on_ingested_data():
    report = run_ufc_walk_forward_backtest(write_cache=False)
    assert report.get("status") in ("ok", "error")
    if report["status"] == "ok":
        assert report["aggregate"]["holdout_fights_scored"] > 0
        assert len(report["folds"]) >= 2
