"""Tests for NTG Sports upgrade APIs."""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.db.database import get_connection, init_db
from app.main import app
from app.services.user_teams import ensure_user_team_tables, follow_team, list_follows


@pytest.fixture
def client():
    init_db()
    return TestClient(app)


def test_performance_endpoints(client):
    r = client.get("/api/performance/summary")
    assert r.status_code == 200
    body = r.json()
    assert "prop_tracker" in body

    r2 = client.get("/api/performance/picks?limit=5")
    assert r2.status_code == 200
    assert "picks" in r2.json()


def test_methodology_and_performance_pages(client):
    assert client.get("/methodology").status_code == 200
    assert client.get("/performance").status_code == 200
    assert client.get("/parlay").status_code == 200


def test_player_prop_context_invalid_market(client):
    r = client.get(
        "/api/players/mlb/592450/prop-context",
        params={"market_type": "unknown_market", "line": 0.5, "side": "over"},
    )
    assert r.status_code == 200
    assert r.json().get("status") == "error"


def test_player_prop_context_includes_game_log(client):
    r = client.get(
        "/api/players/mlb/592450/prop-context",
        params={"market_type": "batter_hits", "line": 0.5, "side": "over", "limit": 5},
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("status") == "ok"
    log = body.get("game_log") or {}
    assert isinstance(log.get("columns"), list)
    assert len(log.get("columns") or []) >= 4
    if log.get("games"):
        assert "stats" in log["games"][0]


def test_player_prop_context_includes_depth(client):
    r = client.get(
        "/api/players/mlb/592450/prop-context",
        params={
            "market_type": "batter_hits",
            "line": 0.5,
            "side": "over",
            "game_id": "777777",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("status") == "ok"
    assert "depth" in body
    assert isinstance(body["depth"].get("badges"), list)


def test_parlay_optimize_empty(client):
    r = client.post("/api/parlay/props/optimize", json={"legs": []})
    assert r.status_code == 200
    assert r.json().get("status") == "empty"


def test_matchup_preview_page(client):
    assert client.get("/preview/mlb/777777").status_code == 200


def test_privacy_terms_pages(client):
    assert client.get("/privacy").status_code == 200
    assert client.get("/terms").status_code == 200


def test_player_profile_includes_game_log(client):
    r = client.get("/api/players/mlb/592450/profile")
    assert r.status_code == 200
    body = r.json()
    if body.get("status") == "ok":
        log = body.get("game_log") or {}
        assert isinstance(log.get("columns"), list)


def test_user_team_follows_require_auth(client):
    r = client.get("/api/user/teams/follows")
    assert r.status_code == 401


def test_user_team_follow_crud():
    init_db()
    conn = get_connection()
    try:
        ensure_user_team_tables(conn)
        conn.execute(
            "INSERT OR IGNORE INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
            ("followtest@example.com", "hash", "2026-01-01T00:00:00Z"),
        )
        conn.commit()
        uid = conn.execute(
            "SELECT id FROM users WHERE email = ?", ("followtest@example.com",)
        ).fetchone()[0]
        follow_team(conn, uid, "mlb", "110")
        follows = list_follows(conn, uid)
        assert any(f["team_id"] == "110" for f in follows)
    finally:
        conn.close()
