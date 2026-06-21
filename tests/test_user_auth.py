"""End-user registration, login, and props gating."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth import user_auth
from app.db.database import get_connection
from app.db.user_schema import ensure_users_table
from app.main import app
from app.services.user_accounts import create_user, mark_email_verified

client = TestClient(app)


def _email(label: str) -> str:
    return f"{label}-{uuid.uuid4().hex[:8]}@example.com"


@pytest.fixture
def production_props_auth(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PROPS_REQUIRE_VERIFIED_USER", "true")
    monkeypatch.setenv("USER_REGISTRATION_ENABLED", "true")
    monkeypatch.setenv("USER_COOKIE_SECURE", "false")
    monkeypatch.delenv("ADMIN_AUTH_DISABLED", raising=False)


@pytest.fixture
def isolated_users(tmp_path, monkeypatch):
    db_path = tmp_path / "test_users.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    conn = get_connection()
    try:
        ensure_users_table(conn)
    finally:
        conn.close()
    yield db_path


def test_register_login_props_flow(production_props_auth, isolated_users):
    fan_email = _email("fan")
    reg = client.post(
        "/api/auth/user/register",
        json={"email": fan_email, "password": "secretpass", "accept_terms": True},
    )
    assert reg.status_code == 200
    body = reg.json()
    assert body["email"] == fan_email

    props = client.get("/api/daily/props?limit=1", cookies=reg.cookies)
    assert props.status_code != 401

    client.post("/api/auth/user/logout", cookies=reg.cookies)
    props_logged_out = client.get("/api/daily/props?limit=1")
    assert props_logged_out.status_code == 401

    login = client.post(
        "/api/auth/user/login",
        json={"email": fan_email, "password": "secretpass"},
    )
    assert login.status_code == 200
    props2 = client.get("/api/daily/props?limit=1", cookies=login.cookies)
    assert props2.status_code != 401


def test_register_requires_terms(production_props_auth, isolated_users):
    fan_email = _email("noterms")
    reg = client.post(
        "/api/auth/user/register",
        json={"email": fan_email, "password": "secretpass", "accept_terms": False},
    )
    assert reg.status_code == 400


def test_verify_email_endpoint(production_props_auth, isolated_users):
    email = _email("verify")
    user, token = create_user(email, "secretpass")
    resp = client.post("/api/auth/user/verify-email", json={"token": token})
    assert resp.status_code == 200
    assert resp.json()["email"] == email
    props = client.get("/api/daily/props?limit=1")
    assert props.status_code != 401


def test_props_public_when_gate_disabled(monkeypatch, isolated_users):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("PROPS_REQUIRE_VERIFIED_USER", "false")
    resp = client.get("/api/daily/props?limit=1")
    assert resp.status_code != 401


def test_password_hash_roundtrip():
    hashed = user_auth.hash_password("hunter2pass")
    assert user_auth.verify_password("hunter2pass", hashed)
    assert not user_auth.verify_password("wrong", hashed)
