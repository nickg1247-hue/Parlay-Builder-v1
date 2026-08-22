from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@pytest.fixture
def maintenance_on(tmp_path: Path, monkeypatch):
    flag = tmp_path / "maintenance.on"
    flag.write_text("ON\n", encoding="utf-8")
    monkeypatch.setenv("MAINTENANCE_FLAG_PATH", str(flag))
    client.cookies.clear()
    yield flag
    client.cookies.clear()


@pytest.fixture
def auth_env(monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "testadmin")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-secret")
    monkeypatch.setenv("ADMIN_SESSION_SECRET", "unit-test-session-secret")
    client.cookies.clear()
    yield
    client.cookies.clear()


def test_site_normal_when_maintenance_off():
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 200
    assert "Under Construction" not in response.text
    assert "NTG Sports" in response.text


def test_flag_file_enables_construction_for_public_pages(maintenance_on):
    for path in ("/", "/mlb", "/mlb/props", "/props", "/performance", "/nfl", "/nba", "/cfb"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 302, path
        assert response.headers["location"] == "/under-construction.html"
        assert "no-store" in response.headers.get("cache-control", "")


def test_construction_page_is_standalone(maintenance_on):
    response = client.get("/under-construction.html")
    assert response.status_code == 200
    text = response.text
    assert "UNDER CONSTRUCTION" in text.upper()
    assert "NTG" in text
    assert "WE'RE BUILDING SOMETHING BETTER" in text.upper()
    assert "prefers-reduced-motion" in text
    assert "97%" not in text
    assert "Launching Friday" not in text
    assert "<script" not in text.lower()
    assert "ADMIN_PASSWORD" not in text
    assert "ODDS_API_KEY" not in text


def test_test_path_sets_preview_cookie_then_homepage(maintenance_on):
    enter = client.get("/test", follow_redirects=False)
    assert enter.status_code == 302
    assert enter.headers["location"] == "/"
    assert enter.cookies.get("ntg_preview") == "1"
    set_cookie = enter.headers.get("set-cookie", "")
    assert "ntg_preview=1" in set_cookie
    assert "Path=/" in set_cookie
    assert "Max-Age=86400" in set_cookie
    assert "samesite=lax" in set_cookie.lower()

    home = client.get("/", follow_redirects=False)
    assert home.status_code == 200
    assert "home-landing" in home.text

    mlb = client.get("/mlb", follow_redirects=False)
    assert mlb.status_code == 200
    props = client.get("/mlb/props", follow_redirects=False)
    assert props.status_code == 200
    performance = client.get("/performance", follow_redirects=False)
    assert performance.status_code == 200


def test_refresh_keeps_preview_access(maintenance_on):
    client.get("/test", follow_redirects=False)
    first = client.get("/mlb", follow_redirects=False)
    second = client.get("/mlb", follow_redirects=False)
    assert first.status_code == 200
    assert second.status_code == 200


def test_exit_clears_preview_and_returns_to_construction(maintenance_on):
    client.get("/test", follow_redirects=False)
    assert client.get("/", follow_redirects=False).status_code == 200

    exit_resp = client.get("/test/exit", follow_redirects=False)
    assert exit_resp.status_code == 302
    assert exit_resp.headers["location"] == "/"

    blocked = client.get("/", follow_redirects=False)
    assert blocked.status_code == 302
    assert blocked.headers["location"] == "/under-construction.html"


def test_deleting_flag_restores_public_site(tmp_path: Path, monkeypatch):
    flag = tmp_path / "maintenance.on"
    flag.write_text("ON\n", encoding="utf-8")
    monkeypatch.setenv("MAINTENANCE_FLAG_PATH", str(flag))
    client.cookies.clear()

    assert client.get("/", follow_redirects=False).status_code == 302
    flag.unlink()
    restored = client.get("/", follow_redirects=False)
    assert restored.status_code == 200
    assert "home-landing" in restored.text


def test_api_and_health_stay_up_during_maintenance(maintenance_on):
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json().get("status") in ("ok", "degraded")

    public_health = client.get("/health")
    assert public_health.status_code == 200

    static_js = client.get("/static/app.js")
    assert static_js.status_code == 200


def test_static_html_is_blocked_without_preview(maintenance_on):
    response = client.get("/static/mlb_slate.html", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/under-construction.html"


def test_admin_auth_still_required_during_preview(maintenance_on, auth_env):
    client.get("/test", follow_redirects=False)
    board = client.get("/mlb/board", follow_redirects=False)
    assert board.status_code == 302
    assert "/login" in board.headers["location"]

    login = client.post(
        "/api/auth/login",
        json={"username": "testadmin", "password": "test-secret"},
    )
    assert login.status_code == 200
    assert client.get("/mlb/board").status_code == 200


def test_login_stays_reachable_during_construction(maintenance_on):
    response = client.get("/login", follow_redirects=False)
    assert response.status_code == 200


def test_admin_session_bypasses_construction(maintenance_on, auth_env):
    login = client.post(
        "/api/auth/login",
        json={"username": "testadmin", "password": "test-secret"},
    )
    assert login.status_code == 200
    home = client.get("/", follow_redirects=False)
    assert home.status_code == 200
    assert "home-landing" in home.text


def test_api_toggles_construction(tmp_path: Path, monkeypatch):
    flag = tmp_path / "maintenance.on"
    monkeypatch.setenv("MAINTENANCE_FLAG_PATH", str(flag))
    monkeypatch.setenv("ADMIN_AUTH_DISABLED", "true")
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("REQUIRE_ADMIN_AUTH", raising=False)
    client.cookies.clear()

    status = client.get("/api/maintenance")
    assert status.status_code == 200
    assert status.json()["enabled"] is False

    turned_on = client.post("/api/maintenance", json={"enabled": True})
    assert turned_on.status_code == 200
    assert turned_on.json()["enabled"] is True
    assert flag.is_file()
    assert client.get("/", follow_redirects=False).status_code == 302

    turned_off = client.post("/api/maintenance", json={"enabled": False})
    assert turned_off.status_code == 200
    assert turned_off.json()["enabled"] is False
    assert not flag.is_file()
    restored = client.get("/", follow_redirects=False)
    assert restored.status_code == 200
    assert "home-landing" in restored.text


def test_api_toggle_requires_admin_when_auth_on(maintenance_on, auth_env):
    blocked = client.post("/api/maintenance", json={"enabled": False})
    assert blocked.status_code == 401
    login = client.post(
        "/api/auth/login",
        json={"username": "testadmin", "password": "test-secret"},
    )
    assert login.status_code == 200
    ok = client.post("/api/maintenance", json={"enabled": False})
    assert ok.status_code == 200
    assert ok.json()["enabled"] is False


def test_cli_toggles_flag_file(tmp_path: Path, monkeypatch):
    from app.auth.maintenance import (
        maintenance_enabled,
        turn_maintenance_off,
        turn_maintenance_on,
    )

    flag = tmp_path / "maintenance.on"
    monkeypatch.setenv("MAINTENANCE_FLAG_PATH", str(flag))
    assert not maintenance_enabled()
    assert turn_maintenance_on() == flag
    assert flag.read_text(encoding="utf-8").strip() == "ON"
    assert maintenance_enabled()
    turn_maintenance_off()
    assert not flag.is_file()
    assert not maintenance_enabled()
