"""Admin auth: public props routes on production."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.auth import admin_auth
from app.main import app

client = TestClient(app)


@pytest.fixture
def production_auth(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ADMIN_PASSWORD", "test-secret")
    monkeypatch.delenv("ADMIN_AUTH_DISABLED", raising=False)


def test_daily_props_public_when_props_gate_disabled(production_auth, monkeypatch):
    monkeypatch.setenv("PROPS_REQUIRE_VERIFIED_USER", "false")
    with patch.object(admin_auth, "auth_enabled", return_value=True):
        with patch.object(admin_auth, "is_authenticated", return_value=False):
            resp = client.get("/api/daily/props?limit=1")
    assert resp.status_code != 401


def test_daily_board_still_protected_when_auth_enabled(production_auth):
    with patch.object(admin_auth, "auth_enabled", return_value=True):
        with patch.object(admin_auth, "is_authenticated", return_value=False):
            resp = client.get("/api/daily")
    assert resp.status_code == 401


def test_prop_slip_export_public_when_auth_enabled(production_auth, monkeypatch):
    monkeypatch.setenv("PROP_SLIP_PUBLIC", "true")
    monkeypatch.setenv("PROPS_REQUIRE_VERIFIED_USER", "false")
    with patch.object(admin_auth, "auth_enabled", return_value=True):
        with patch.object(admin_auth, "is_authenticated", return_value=False):
            with patch(
                "app.main.export_slip_for_bookmaker",
                return_value={"export_text": "test", "legs": []},
            ):
                resp = client.post(
                    "/api/props/slip/export",
                    json={"legs": [], "bookmaker": "draftkings"},
                )
    assert resp.status_code != 401


def test_prop_slip_export_hidden_in_production_by_default(production_auth, monkeypatch):
    monkeypatch.setenv("PROPS_REQUIRE_VERIFIED_USER", "false")
    with patch.object(admin_auth, "auth_enabled", return_value=True):
        with patch.object(admin_auth, "is_authenticated", return_value=False):
            resp = client.post(
                "/api/props/slip/export",
                json={"legs": [], "bookmaker": "draftkings"},
            )
    assert resp.status_code == 404
