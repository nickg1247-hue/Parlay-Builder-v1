"""SSR pages must not call external HTTP APIs (prevents 504 gateway timeouts)."""

from __future__ import annotations

import time
from urllib.parse import urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app


def _is_external_url(url: str) -> bool:
    parsed = urlparse(str(url))
    return parsed.scheme in ("http", "https") and parsed.netloc not in (
        "testserver",
        "127.0.0.1",
        "localhost",
    )


@pytest.fixture
def block_external_http(monkeypatch):
    real_request = httpx.Client.request

    def guarded_request(self, method, url, *args, **kwargs):
        if _is_external_url(url):
            raise AssertionError(
                f"Unexpected external HTTP during SSR: {method} {url}"
            )
        return real_request(self, method, url, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "request", guarded_request)


def test_home_page_no_external_http(block_external_http):
    client = TestClient(app)
    t0 = time.perf_counter()
    res = client.get("/")
    elapsed = time.perf_counter() - t0
    assert res.status_code == 200
    assert 'id="ntg-page-data"' in res.text
    assert elapsed < 8.0, f"home page took {elapsed:.1f}s"


def test_mlb_slate_no_external_http(block_external_http):
    client = TestClient(app)
    t0 = time.perf_counter()
    res = client.get("/mlb")
    elapsed = time.perf_counter() - t0
    assert res.status_code == 200
    assert "mlb_slate" in res.text
    assert elapsed < 8.0, f"mlb slate took {elapsed:.1f}s"


def test_mlb_game_page_no_external_http(block_external_http, cached_mlb_game):
    game_date, game_id = cached_mlb_game
    client = TestClient(app)
    t0 = time.perf_counter()
    res = client.get(f"/mlb/game/{game_id}?date={game_date}")
    elapsed = time.perf_counter() - t0
    assert res.status_code == 200
    assert "mlb_game" in res.text
    assert elapsed < 8.0, f"game page took {elapsed:.1f}s"
