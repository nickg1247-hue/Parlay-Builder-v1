"""Watchlist save/list/delete — original line is preserved."""

import uuid

from fastapi.testclient import TestClient

from app.db.database import get_connection
from app.db.user_schema import ensure_users_table
from app.main import app
from app.services.user_accounts import create_user
from app.services.watchlist import ensure_watchlist_tables

client = TestClient(app)


def _register_and_login(tmp_path, monkeypatch):
    db_path = tmp_path / "watchlist.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("USER_REGISTRATION_ENABLED", "true")
    monkeypatch.setenv("USER_COOKIE_SECURE", "false")
    conn = get_connection()
    try:
        ensure_users_table(conn)
        ensure_watchlist_tables(conn)
    finally:
        conn.close()
    email = f"wl-{uuid.uuid4().hex[:8]}@example.com"
    create_user(email, "password12")
    login = client.post(
        "/api/auth/user/login",
        json={"email": email, "password": "password12"},
    )
    assert login.status_code == 200
    return email


def test_watchlist_requires_sign_in():
    client.cookies.clear()
    res = client.get("/api/watchlist")
    assert res.status_code == 401


def test_watchlist_page_renders():
    res = client.get("/watchlist")
    assert res.status_code == 200
    assert "My Picks" in res.text


def test_player_page_renders():
    res = client.get("/players/nfl/Justin%20Jefferson")
    assert res.status_code == 200
    assert "Available props" in res.text


def test_save_and_remove_prop_keeps_original_line(tmp_path, monkeypatch):
    _register_and_login(tmp_path, monkeypatch)
    saved = client.post(
        "/api/watchlist/props",
        json={
            "sport": "nfl",
            "player_name": "Justin Jefferson",
            "market_type": "player_reception_yds",
            "market_label": "Receiving Yards",
            "side": "over",
            "line": 84.5,
            "sportsbook": "draftkings",
            "odds": -110,
            "game_id": "nfl-1",
            "matchup": "MIN vs GB",
            "projection": 96.2,
            "model_probability": 0.618,
        },
    )
    assert saved.status_code == 200
    listed = client.get("/api/watchlist")
    assert listed.status_code == 200
    props = listed.json()["props"]
    assert len(props) == 1
    assert props[0]["saved_line"] == 84.5
    assert props[0]["player"] == "Justin Jefferson"
    assert "current_line" in props[0]
    assert props[0]["saved_line"] == 84.5
    assert props[0]["saved_projection"] == 96.2
    assert props[0]["saved_model_probability"] == 0.618
    removed = client.delete(f"/api/watchlist/props/{props[0]['id']}")
    assert removed.status_code == 200
    assert client.get("/api/watchlist").json()["props"] == []
