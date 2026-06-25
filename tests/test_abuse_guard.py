"""Expensive query guards (refresh / scan / live_test)."""

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.services.abuse_guard import assert_expensive_query_allowed


def _request() -> Request:
    scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
    return Request(scope)


def test_refresh_blocked_for_anonymous():
    with pytest.raises(HTTPException) as exc:
        assert_expensive_query_allowed(_request(), refresh=True)
    assert exc.value.status_code == 403


def test_noop_when_flags_false():
    assert_expensive_query_allowed(_request(), refresh=False, scan=False) is None
