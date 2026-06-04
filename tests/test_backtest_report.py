from fastapi.testclient import TestClient

from app.main import app
from app.services.backtest_report import _empty_report, run_backtest_report

client = TestClient(app)

REQUIRED_KEYS = {
    "generated_at",
    "days",
    "start_date",
    "end_date",
    "games_in_window",
    "moneyline",
    "totals",
    "status",
    "error",
}
MONEYLINE_KEYS = {
    "games_with_odds",
    "winner_accuracy_pct",
    "plus_ev_picks",
    "plus_ev_accuracy_pct",
    "log_loss_model",
    "log_loss_market",
    "model_beats_market",
    "min_edge",
}
TOTALS_KEYS = {
    "games_with_ou_line",
    "ou_pick_accuracy_pct",
    "plus_ev_ou_picks",
    "plus_ev_ou_accuracy_pct",
    "total_runs_mae",
    "total_runs_bias",
    "min_edge",
}


def test_empty_report_shape():
    report = _empty_report(30, "test error")
    assert REQUIRED_KEYS <= set(report.keys())
    assert MONEYLINE_KEYS <= set(report["moneyline"].keys())
    assert TOTALS_KEYS <= set(report["totals"].keys())
    assert report["days"] == 30
    assert report["status"] == "error"
    assert report["error"] == "test error"


def test_run_backtest_report_shape():
    report = run_backtest_report(7, write_cache=False)
    assert REQUIRED_KEYS <= set(report.keys())
    assert MONEYLINE_KEYS <= set(report["moneyline"].keys())
    assert TOTALS_KEYS <= set(report["totals"].keys())
    assert report["days"] == 7
    assert report["status"] in ("ok", "error")


def test_api_backtest_endpoint():
    response = client.get("/api/backtest?days=7")
    assert response.status_code == 200
    body = response.json()
    assert REQUIRED_KEYS <= set(body.keys())
