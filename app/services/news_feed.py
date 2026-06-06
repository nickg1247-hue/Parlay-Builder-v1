"""RSS sports headlines for home page (Phase D)."""

from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import httpx

from app.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

CACHE_PATH = PROJECT_ROOT / "data" / "processed" / "news_cache.json"
CACHE_TTL_SECONDS = 15 * 60
MAX_ITEMS = 10
REQUEST_TIMEOUT = 20.0

FEEDS: list[tuple[str, str]] = [
    ("ESPN Top", "https://www.espn.com/espn/rss/news"),
    ("ESPN MLB", "https://www.espn.com/espn/rss/mlb/news"),
]


def _parse_pub_date(raw: str | None) -> str | None:
    if not raw or not raw.strip():
        return None
    try:
        dt = parsedate_to_datetime(raw.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return raw.strip()


def _strip_html(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = re.sub(r"<[^>]+>", "", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


def _parse_rss(xml_text: str, source: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    items: list[dict[str, Any]] = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        items.append(
            {
                "title": title,
                "link": link,
                "published": _parse_pub_date(item.findtext("pubDate")),
                "source": source,
                "summary": _strip_html(item.findtext("description")),
            }
        )
    return items


def _fetch_feed(source: str, url: str) -> list[dict[str, Any]]:
    with httpx.Client(timeout=REQUEST_TIMEOUT, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
    return _parse_rss(response.text, source)


def _load_cache() -> dict[str, Any] | None:
    if not CACHE_PATH.exists():
        return None
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read news cache: %s", exc)
        return None


def _write_cache(payload: dict[str, Any]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _cache_age_seconds(cached_at: str) -> float:
    try:
        ts = datetime.fromisoformat(cached_at.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds()
    except ValueError:
        return CACHE_TTL_SECONDS + 1


def clear_news_cache() -> None:
    """Reset cache file (tests only)."""
    if CACHE_PATH.exists():
        CACHE_PATH.unlink()


def get_news_headlines(*, force_refresh: bool = False) -> dict[str, Any]:
    """Return up to 10 headlines; cache 15 minutes on disk."""
    cached = _load_cache()
    if (
        not force_refresh
        and cached
        and cached.get("cached_at")
        and _cache_age_seconds(cached["cached_at"]) < CACHE_TTL_SECONDS
    ):
        return {**cached, "cache_hit": True}

    errors: list[str] = []
    merged: list[dict[str, Any]] = []
    seen_links: set[str] = set()

    for source, url in FEEDS:
        try:
            for item in _fetch_feed(source, url):
                link = item["link"]
                if link in seen_links:
                    continue
                seen_links.add(link)
                merged.append(item)
        except (httpx.HTTPError, ET.ParseError, ValueError) as exc:
            logger.warning("News feed failed for %s: %s", source, exc)
            errors.append(f"{source}: {exc}")

    merged.sort(key=lambda x: x.get("published") or "", reverse=True)
    items = merged[:MAX_ITEMS]

    if not items and cached and cached.get("items"):
        stale = {**cached, "cache_hit": True, "stale": True}
        if errors:
            stale["errors"] = errors
        return stale

    payload: dict[str, Any] = {
        "cached_at": datetime.now(timezone.utc).isoformat(),
        "cache_ttl_seconds": CACHE_TTL_SECONDS,
        "items": items,
        "count": len(items),
        "cache_hit": False,
    }
    if errors:
        payload["errors"] = errors
    if not items:
        payload["error"] = "Headlines unavailable — try again shortly."

    _write_cache(payload)
    return payload
