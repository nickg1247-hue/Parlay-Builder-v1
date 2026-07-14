"""Block bare/public scraping of pick/score JSON APIs — allow site browser loads."""

from __future__ import annotations

import os
from typing import Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.auth.admin_auth import auth_enabled, is_authenticated

_BLOCKED_EXACT = frozenset({
    "/api/home/today",
    "/api/scores/today",
    "/api/props/search",
})


def public_api_gate_enabled() -> bool:
    raw = os.getenv("PUBLIC_API_GATE", "true").strip().lower()
    return raw not in ("0", "false", "no", "off")


def is_blocked_public_get(path: str) -> bool:
    return path in _BLOCKED_EXACT


def _admin_api_bypass(request: Request) -> bool:
    """Allow blocked GET /api only when admin auth is on and session is valid."""
    if not auth_enabled():
        return False
    return is_authenticated(request)


def is_site_browser_request(request: Request) -> bool:
    """
    True for loads from our own pages (same-origin fetch / navigation).

    Browsers send Sec-Fetch-Site on fetch(); curl/scripts typically do not.
    FetchJSON also sends X-NTG-Client: site as a secondary signal.
    """
    client = (request.headers.get("x-ntg-client") or "").strip().lower()
    if client == "site":
        return True
    site = (request.headers.get("sec-fetch-site") or "").strip().lower()
    return site in ("same-origin", "same-site")


class PublicApiGateMiddleware(BaseHTTPMiddleware):
    """403 for blocked GET /api when the caller is not our site or an admin session."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable):
        if not public_api_gate_enabled():
            return await call_next(request)
        if request.method.upper() != "GET":
            return await call_next(request)
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)
        if not is_blocked_public_get(path):
            return await call_next(request)
        if is_site_browser_request(request) or _admin_api_bypass(request):
            return await call_next(request)
        return JSONResponse(
            status_code=403,
            content={
                "detail": (
                    "Direct API access is disabled. "
                    "Open the site in your browser to view games and picks."
                ),
                "code": "public_api_disabled",
            },
        )
