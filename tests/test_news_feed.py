"""Phase D news feed tests."""

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import news_feed as nf

client = TestClient(app)

SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Test headline</title>
      <link>https://example.com/a</link>
      <pubDate>Sat, 06 Jun 2026 12:00:00 GMT</pubDate>
      <description><![CDATA[Summary text]]></description>
    </item>
  </channel>
</rss>"""


@pytest.fixture
def isolated_news(tmp_path, monkeypatch):
    path = tmp_path / "news_cache.json"
    monkeypatch.setattr(nf, "CACHE_PATH", path)
    return path


def test_parse_rss_items():
    items = nf._parse_rss(SAMPLE_RSS, "ESPN Test")
    assert len(items) == 1
    assert items[0]["title"] == "Test headline"
    assert items[0]["link"] == "https://example.com/a"
    assert items[0]["source"] == "ESPN Test"
    assert items[0]["summary"] == "Summary text"
    assert items[0]["published"] is not None


@patch("app.services.news_feed._fetch_feed")
def test_get_news_headlines_caches(mock_fetch, isolated_news):
    mock_fetch.return_value = [
        {
            "title": "A",
            "link": "https://example.com/1",
            "published": "2026-06-06T12:00:00+00:00",
            "source": "ESPN Top",
            "summary": None,
        }
    ]
    first = nf.get_news_headlines(force_refresh=True)
    second = nf.get_news_headlines()
    assert first["count"] == 1
    assert second["cache_hit"] is True
    assert isolated_news.exists()


def test_api_news_endpoint():
    with patch(
        "app.main.get_news_headlines",
        return_value={"items": [{"title": "X", "link": "https://x", "source": "ESPN"}], "count": 1},
    ):
        resp = client.get("/api/news")
    assert resp.status_code == 200
    assert resp.json()["items"][0]["title"] == "X"
