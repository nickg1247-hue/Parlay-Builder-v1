import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


@pytest.fixture
def auth_env(monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "testadmin")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-secret")
    monkeypatch.setenv("ADMIN_SESSION_SECRET", "unit-test-session-secret")
    client.cookies.clear()
    yield
    client.cookies.clear()


def _login(username="testadmin", password="test-secret"):
    return client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )


def test_board_public_when_auth_disabled(monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("REQUIRE_ADMIN_AUTH", raising=False)
    monkeypatch.delenv("ADMIN_AUTH_DISABLED", raising=False)
    response = client.get("/mlb/board")
    assert response.status_code == 200


def test_production_locks_board_without_password(monkeypatch):
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    response = client.get("/mlb/board", follow_redirects=False)
    assert response.status_code == 302
    assert "/login" in response.headers["location"]


def test_static_board_html_blocked_when_auth_enabled(auth_env):
    response = client.get("/static/mlb.html", follow_redirects=False)
    assert response.status_code == 302
    assert "/login" in response.headers["location"]


def test_board_redirects_to_login_when_auth_enabled(auth_env):
    response = client.get("/mlb/board", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].startswith("/login?next=")


def test_lab_and_nba_board_protected(auth_env):
    for path in ("/sandbox", "/mlb/board", "/mlb/lab", "/nba/board", "/nba/board/factors"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 302
        assert "/login" in response.headers["location"]


def test_login_grants_board_access(auth_env):
    login_resp = _login()
    assert login_resp.status_code == 200
    assert "ntg_admin" in login_resp.cookies

    sandbox = client.get("/sandbox")
    assert sandbox.status_code == 200
    assert "Sandbox" in sandbox.text

    board = client.get("/mlb/board")
    assert board.status_code == 200
    assert "MLB Daily Board" in board.text


def test_wrong_password_rejected(auth_env):
    response = _login(password="wrong")
    assert response.status_code == 401


def test_protected_api_requires_auth(auth_env):
    response = client.get("/api/daily")
    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"


def test_protected_api_works_after_login(auth_env):
    _login()
    response = client.get("/api/lab/meta")
    assert response.status_code == 200
    assert "moneyline" in response.json()


def test_logout_clears_session(auth_env):
    _login()
    assert client.get("/mlb/board").status_code == 200
    client.post("/api/auth/logout")
    assert client.get("/mlb/board", follow_redirects=False).status_code == 302


def test_public_routes_stay_open_with_auth(auth_env):
    for path in ("/", "/mlb", "/nba", "/health"):
        response = client.get(path)
        assert response.status_code == 200


def test_auth_status_reflects_state(auth_env):
    status = client.get("/api/auth/status").json()
    assert status["auth_enabled"] is True
    assert status["authenticated"] is False
    _login()
    status = client.get("/api/auth/status").json()
    assert status["authenticated"] is True
