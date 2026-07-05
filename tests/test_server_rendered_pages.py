"""Tests for server-rendered MLB pages and public API gate."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.auth.public_api_gate import is_blocked_public_get
from app.config import PROJECT_ROOT
from app.main import app
from app.services.page_render import render_static_page

STATIC_DIR = PROJECT_ROOT / "static"

_MIN_HOME = {
    "kind": "home",
    "date": "2026-07-04",
    "summary": {},
    "scores": {"games": []},
    "odds": {},
    "status": {},
    "propsData": {},
    "trackerSummary": {},
    "perfSummary": {},
    "tickerScores": {"games": []},
    "build": {"build_id": "test"},
}

_MIN_SLATE = {
    "kind": "mlb_slate",
    "date": "2026-07-04",
    "slate": {"games": []},
    "summary": {},
    "odds": {},
    "status": {},
    "tickerScores": {"games": []},
}


def test_is_blocked_public_get_paths():
    assert is_blocked_public_get("/api/home/today")
    assert is_blocked_public_get("/api/scores/today")
    assert is_blocked_public_get("/api/props/search")
    assert not is_blocked_public_get("/api/games/mlb/123/insights")
    assert not is_blocked_public_get("/api/auth/status")
    assert not is_blocked_public_get("/api/schedule/mlb")


def test_render_static_page_injects_json():
    html = render_static_page(
        STATIC_DIR,
        "offline.html",
        {"kind": "test", "value": 1},
    ).body.decode("utf-8")
    assert 'id="ntg-page-data"' in html
    assert "page-data.js" in html
    assert '"kind": "test"' in html or '"kind":"test"' in html.replace(" ", "")


def test_public_api_gate_blocks_home_today():
    client = TestClient(app)
    res = client.get("/api/home/today")
    assert res.status_code == 403
    assert res.json().get("code") == "public_api_disabled"


@patch("app.main.build_home_page_data", new_callable=AsyncMock, return_value=_MIN_HOME)
def test_home_page_embeds_page_data(_mock_home):
    client = TestClient(app)
    res = client.get("/")
    assert res.status_code == 200
    assert 'id="ntg-page-data"' in res.text
    assert '"kind"' in res.text and "home" in res.text


@patch("app.main.build_mlb_slate_page_data", new_callable=AsyncMock, return_value=_MIN_SLATE)
def test_mlb_slate_embeds_page_data(_mock_slate):
    client = TestClient(app)
    res = client.get("/mlb")
    assert res.status_code == 200
    assert 'id="ntg-page-data"' in res.text
    assert "mlb_slate" in res.text


@patch("app.services.mlb_page_data.build_daily_top_props", return_value={"top_props": []})
@patch("app.services.mlb_page_data.local_mlb_scores", return_value={"games": []})
@patch("app.services.mlb_page_data.get_home_today_summary", return_value={})
@patch("app.services.mlb_page_data.get_today_snapshot", return_value={})
@patch("app.services.mlb_page_data.get_refresh_status", return_value={})
@patch("app.services.mlb_page_data.summarize_prop_tracker", return_value={})
@patch("app.services.mlb_page_data.performance_summary_payload", return_value={})
def test_build_home_page_data_calls_props_builder(*_mocks):
    from app.services.mlb_page_data import build_home_page_data

    data = asyncio.run(build_home_page_data())
    assert data["kind"] == "home"
    assert "propsData" in data
