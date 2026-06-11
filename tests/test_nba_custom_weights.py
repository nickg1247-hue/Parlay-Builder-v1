"""Tests for global NBA custom factor weights."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import nba_custom_weights as cw

client = TestClient(app)


@pytest.fixture
def weights_path(tmp_path, monkeypatch):
    path = tmp_path / "nba_custom_weights.json"
    monkeypatch.setattr(cw, "WEIGHTS_PATH", path)
    return path


def test_adjust_weight_pct_keeps_total_100():
    pct = cw._normalize_pct_weights(cw._pct_weights(cw.DEFAULT_FACTORS))
    bumped = cw.adjust_weight_pct(pct, "team_offensive_rating", 2)
    assert sum(bumped.values()) == 100
    assert bumped["team_offensive_rating"] == pct["team_offensive_rating"] + 2


def test_save_and_load_round_trip(weights_path):
    cfg = cw.load_custom_weights_config()
    cfg["factors"]["home_court_advantage"] = 0.10
    cw.save_custom_weights_config(cfg)
    loaded = cw.load_custom_weights_config()
    assert abs(loaded["factors"]["home_court_advantage"] - 0.10) < 0.02


@pytest.fixture
def auth_env(monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "testadmin")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-secret")
    monkeypatch.setenv("ADMIN_SESSION_SECRET", "unit-test-session-secret")
    client.cookies.clear()
    yield
    client.cookies.clear()


def _login():
    return client.post(
        "/api/auth/login",
        json={"username": "testadmin", "password": "test-secret"},
    )


def test_custom_weights_api_requires_auth(auth_env):
    assert client.get("/api/nba/custom-weights").status_code == 401


def test_custom_weights_get_put_reset(auth_env, weights_path):
    _login()
    get_resp = client.get("/api/nba/custom-weights")
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["total_pct"] == 100
    assert len(body["factors"]) == 16

    factors = {f["key"]: f["weight"] for f in body["factors"]}
    factors["home_court_advantage"] = 0.09
    put_resp = client.put("/api/nba/custom-weights", json={"factors": factors})
    assert put_resp.status_code == 200
    assert weights_path.exists()

    reset_resp = client.post("/api/nba/custom-weights/reset")
    assert reset_resp.status_code == 200
    assert reset_resp.json()["total_pct"] == 100


def test_factors_page_protected(auth_env):
    resp = client.get("/nba/board/factors", follow_redirects=False)
    assert resp.status_code == 302

    _login()
    page = client.get("/nba/board/factors")
    assert page.status_code == 200
    assert "Factor weights" in page.text


def test_sandbox_page_protected(auth_env):
    resp = client.get("/sandbox", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.headers["location"]

    _login()
    page = client.get("/sandbox")
    assert page.status_code == 200
    assert "Advanced board" in page.text
    assert "/mlb/lab" in page.text
