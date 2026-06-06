"""Model Lab API and anti-fake preflight tests."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.model_lab import (
    SPLIT_BANNER,
    TRAIN_MAX_SEASON,
    VAL_SEASON,
    goal_gap_pct,
    goal_within_tolerance,
    run_preflight_moneyline,
    run_preflight_totals,
    totals_feature_set_registry,
)

client = TestClient(app)


def test_goal_gap_and_tolerance_log_loss():
    assert goal_gap_pct("log_loss_model", 0.68, 0.68) == 0.0
    assert goal_gap_pct("log_loss_model", 0.70, 0.68) == pytest.approx(0.0294, rel=1e-2)
    assert goal_within_tolerance("log_loss_model", 0.70, 0.68, 0.05)
    assert not goal_within_tolerance("log_loss_model", 0.72, 0.68, 0.05)


def test_goal_gap_accuracy_higher_is_better():
    assert goal_gap_pct("winner_accuracy_pct", 55.0, 52.0) == 0.0
    assert goal_within_tolerance("winner_accuracy_pct", 50.0, 52.0, 0.05)


def test_lab_meta():
    response = client.get("/api/lab/meta")
    assert response.status_code == 200
    body = response.json()
    assert body["splits"] == SPLIT_BANNER
    assert body["tracks"] == ["moneyline", "totals"]
    assert "v1_baseline" in body["moneyline"]["feature_sets"]
    assert "totals_full" in body["totals"]["feature_sets"]
    assert "log_loss_model" in body["moneyline"]["goal_metrics"]
    assert "totals_log_loss_model" in body["totals"]["goal_metrics"]


def test_lab_page():
    response = client.get("/mlb/lab")
    assert response.status_code == 200
    assert "MLB Model Lab" in response.text
    assert "track-select" in response.text
    assert "mlb_lab.js" in response.text


def test_lab_runs_list_empty_or_ok():
    response = client.get("/api/lab/runs")
    assert response.status_code == 200
    assert "runs" in response.json()


def test_shuffle_label_preflight_moneyline():
    import pandas as pd

    leaky = pd.DataFrame(
        {
            "home_win": [1, 0, 1, 0, 1, 0] * 20,
            "home_pitcher_era": list(range(120)),
            "away_pitcher_era": list(range(120, 240)),
            "home_last10_win_pct": [0.5] * 120,
            "away_last10_win_pct": [0.5] * 120,
            "home_last10_run_diff": [0.0] * 120,
            "away_last10_run_diff": [0.0] * 120,
            "home_rest_days": [1.0] * 120,
            "away_rest_days": [1.0] * 120,
        }
    )
    leaky["home_win"] = leaky.index % 2
    result = run_preflight_moneyline(list(leaky.columns[:8]), leaky)
    assert "shuffle_labels" in result


def test_totals_feature_registry():
    reg = totals_feature_set_registry()
    assert "totals_full" in reg
    assert "h2h_avg_total_runs" in reg["totals_full"]
    assert "h2h_avg_total_runs" not in reg["totals_no_h2h"]


@pytest.mark.slow
def test_lab_run_v1_baseline():
    response = client.post(
        "/api/lab/run",
        json={
            "experiment_id": "test-v1",
            "track": "moneyline",
            "feature_set": "v1_baseline",
            "goal_metric": "log_loss_model",
            "goal_value": 0.99,
        },
    )
    if response.status_code == 400:
        pytest.skip("Games data not available")
    assert response.status_code == 200
    body = response.json()
    assert body["track"] == "moneyline"
    assert body["experiment_id"] == "test-v1"
    assert body["splits"]["train"] == f"seasons ≤ {TRAIN_MAX_SEASON}"
    assert body["validation_summary"]["season"] == VAL_SEASON
    assert body["preflight"]["passed"] is True

    detail = client.get(f"/api/lab/runs/{body['id']}")
    assert detail.status_code == 200


@pytest.mark.slow
def test_lab_run_totals_track():
    response = client.post(
        "/api/lab/run",
        json={
            "experiment_id": "test-totals",
            "track": "totals",
            "feature_set": "totals_full",
            "goal_metric": "totals_log_loss_model",
            "goal_value": 0.99,
        },
    )
    if response.status_code == 400:
        pytest.skip("Games data not available")
    assert response.status_code == 200
    body = response.json()
    assert body["track"] == "totals"
    assert body["preflight"]["track"] == "totals"
    assert "totals" in body["validation_summary"]
    assert body["learning_curve"]


def test_lab_run_unknown_feature_set():
    response = client.post(
        "/api/lab/run",
        json={
            "experiment_id": "bad",
            "track": "moneyline",
            "feature_set": "not_a_real_set",
            "goal_metric": "log_loss_model",
            "goal_value": 0.5,
        },
    )
    assert response.status_code == 400


def test_lab_run_unknown_totals_feature_set():
    response = client.post(
        "/api/lab/run",
        json={
            "experiment_id": "bad-totals",
            "track": "totals",
            "feature_set": "not_a_totals_set",
            "goal_metric": "totals_log_loss_model",
            "goal_value": 0.7,
        },
    )
    assert response.status_code == 400


def test_lab_run_missing_track():
    response = client.post(
        "/api/lab/run",
        json={
            "experiment_id": "no-track",
            "feature_set": "v1_baseline",
            "goal_metric": "log_loss_model",
            "goal_value": 0.5,
        },
    )
    assert response.status_code == 422


@pytest.mark.slow
def test_confirm_totals_gate():
    run_resp = client.post(
        "/api/lab/run",
        json={
            "experiment_id": "confirm-totals",
            "track": "totals",
            "feature_set": "totals_full",
            "goal_metric": "total_runs_mae",
            "goal_value": 99.0,
        },
    )
    if run_resp.status_code == 400:
        pytest.skip("Games data not available")
    assert run_resp.status_code == 200
    run = run_resp.json()
    if not run.get("goal_met"):
        pytest.skip("Totals validation goal not met in this environment")

    confirm_resp = client.post(
        "/api/lab/confirm-test",
        json={"run_id": run["id"], "promote": False},
    )
    assert confirm_resp.status_code == 200
    body = confirm_resp.json()
    gate = body["test_confirm"]["production_gate"]
    assert gate["track"] == "totals"
    assert "totals_gate_passed" in gate
    assert "active_gate_passed" in gate
    assert body["test_confirm"]["totals"]["log_loss_market"] is not None


def test_feature_set_queue_starts_with_pick():
    from app.services.model_lab import _feature_set_queue

    q = _feature_set_queue("moneyline", "v1_baseline")
    assert q[0] == "v1_baseline"
    assert len(q) > 1


@pytest.mark.slow
def test_lab_run_single_no_campaign():
    response = client.post(
        "/api/lab/run",
        json={
            "experiment_id": "single-run",
            "track": "moneyline",
            "feature_set": "v1_baseline",
            "goal_metric": "log_loss_model",
            "goal_value": 0.99,
            "until_within_pct": 0,
        },
    )
    if response.status_code == 400:
        pytest.skip("Games data not available")
    assert response.status_code == 200
    body = response.json()
    assert body.get("campaign") is None
    assert "metric_actual_value" in body
    assert "goal_gap_pct" in body


@pytest.mark.slow
def test_lab_run_until_within_campaign():
    response = client.post(
        "/api/lab/run",
        json={
            "experiment_id": "auto-run",
            "track": "moneyline",
            "feature_set": "v1_baseline",
            "goal_metric": "log_loss_model",
            "goal_value": 0.99,
            "until_within_pct": 0.05,
        },
    )
    if response.status_code == 400:
        pytest.skip("Games data not available")
    assert response.status_code == 200
    body = response.json()
    campaign = body.get("campaign")
    assert campaign is not None
    assert campaign["attempts_count"] >= 1
    assert "feature_sets_tried" in campaign
    assert "stopped_reason" in campaign
    assert len(campaign["attempts"]) == campaign["attempts_count"]
    runs_resp = client.get("/api/lab/runs")
    assert runs_resp.status_code == 200
    assert len(runs_resp.json()["runs"]) >= campaign["attempts_count"]


@pytest.mark.slow
def test_confirm_within_tolerance_without_goal_met():
    run_resp = client.post(
        "/api/lab/run",
        json={
            "experiment_id": "tol-confirm",
            "track": "moneyline",
            "feature_set": "v1_baseline",
            "goal_metric": "log_loss_model",
            "goal_value": 0.50,
            "until_within_pct": 0.05,
        },
    )
    if run_resp.status_code == 400:
        pytest.skip("Games data not available")
    assert run_resp.status_code == 200
    run = run_resp.json()
    if not run.get("goal_within_tolerance") and not run.get("goal_met"):
        pytest.skip("Run not within tolerance in this environment")

    confirm_resp = client.post(
        "/api/lab/confirm-test",
        json={"run_id": run["id"], "promote": False},
    )
    assert confirm_resp.status_code == 200


def test_confirm_promote_gate_failure():
    """Promote without passing gate returns 400."""
    run_resp = client.post(
        "/api/lab/run",
        json={
            "experiment_id": "promote-fail",
            "track": "moneyline",
            "feature_set": "v1_baseline",
            "goal_metric": "log_loss_model",
            "goal_value": 0.99,
            "until_within_pct": 0,
        },
    )
    if run_resp.status_code == 400:
        pytest.skip("Games data not available")
    run = run_resp.json()
    if not (run.get("goal_met") or run.get("goal_within_tolerance")):
        pytest.skip("Run not eligible for confirm in this environment")

    confirm_resp = client.post(
        "/api/lab/confirm-test",
        json={"run_id": run["id"], "promote": False},
    )
    if confirm_resp.status_code != 200:
        pytest.skip("Confirm unavailable in this environment")
    confirmed = confirm_resp.json()
    gate = confirmed["test_confirm"]["production_gate"]
    if gate.get("active_gate_passed"):
        pytest.skip("Gate passed — cannot test failure path")

    promote_resp = client.post(
        "/api/lab/confirm-test",
        json={"run_id": run["id"], "promote": True},
    )
    assert promote_resp.status_code == 400


def test_confirm_requires_goal_met():
    response = client.post(
        "/api/lab/confirm-test",
        json={"run_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert response.status_code == 400
