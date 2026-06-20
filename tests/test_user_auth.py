"""End-user registration, verification, and props gating."""

import uuid
from unittest.mock import patch

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


def test_register_login_verify_flow(production_props_auth, isolated_users):
    fan_email = _email("fan")
    verified_email = _email("verified")
    with patch("app.main.send_verification_email", return_value=True):
        reg = client.post(
            "/api/auth/user/register",
            json={"email": fan_email, "password": "secretpass"},
        )
    assert reg.status_code == 200
    body = reg.json()
    assert body["email"] == fan_email

    login = client.post(
        "/api/auth/user/login",
        json={"email": fan_email, "password": "secretpass"},
    )
    assert login.status_code == 200
    assert login.json()["email_verified"] is False

    props = client.get("/api/daily/props?limit=1")
    assert props.status_code == 401

    user, token = create_user(verified_email, "secretpass")
    mark_email_verified(user["id"])
    login2 = client.post(
        "/api/auth/user/login",
        json={"email": verified_email, "password": "secretpass"},
    )
    assert login2.status_code == 200
    props2 = client.get("/api/daily/props?limit=1")
    assert props2.status_code != 401


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
