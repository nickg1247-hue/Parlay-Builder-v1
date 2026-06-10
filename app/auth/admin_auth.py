"""Session cookie auth for advanced boards and model lab (optional via ADMIN_PASSWORD)."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from typing import Callable

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

COOKIE_NAME = "ntg_admin"
SESSION_MAX_AGE_SECONDS = 7 * 24 * 3600

PROTECTED_PAGE_PATHS = frozenset({"/mlb/board", "/mlb/lab", "/nba/board"})
PROTECTED_API_PREFIXES = (
    "/api/daily",
    "/api/nba/daily",
    "/api/backtest",
    "/api/clv/summary",
    "/api/lab/",
)


def auth_enabled() -> bool:
    return bool(os.getenv("ADMIN_PASSWORD", "").strip())


def admin_username() -> str:
    return os.getenv("ADMIN_USERNAME", "admin").strip() or "admin"


def _session_secret() -> bytes:
    explicit = os.getenv("ADMIN_SESSION_SECRET", "").strip()
    if explicit:
        return explicit.encode()
    password = os.getenv("ADMIN_PASSWORD", "")
    return hashlib.sha256(f"ntg-session:{password}".encode()).digest()


def verify_credentials(username: str, password: str) -> bool:
    if not auth_enabled():
        return True
    user_ok = secrets.compare_digest(username, admin_username())
    pass_ok = secrets.compare_digest(password, os.getenv("ADMIN_PASSWORD", ""))
    return user_ok and pass_ok


def _sign_payload(payload: str) -> str:
    sig = hmac.new(_session_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _verify_token(token: str) -> bool:
    if not token or "." not in token:
        return False
    payload, sig = token.rsplit(".", 1)
    expected = hmac.new(_session_secret(), payload.encode(), hashlib.sha256).hexdigest()
    if not secrets.compare_digest(sig, expected):
        return False
    parts = payload.split("|")
    if len(parts) != 2:
        return False
    user, exp_str = parts
    if user != admin_username():
        return False
    try:
        exp = int(exp_str)
    except ValueError:
        return False
    return exp >= int(time.time())


def create_session_token() -> str:
    exp = int(time.time()) + SESSION_MAX_AGE_SECONDS
    return _sign_payload(f"{admin_username()}|{exp}")


def is_authenticated(request: Request) -> bool:
    if not auth_enabled():
        return True
    token = request.cookies.get(COOKIE_NAME, "")
    return _verify_token(token)


def set_session_cookie(response: Response) -> None:
    secure = os.getenv("APP_ENV", "development").lower() == "production"
    response.set_cookie(
        key=COOKIE_NAME,
        value=create_session_token(),
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    secure = os.getenv("APP_ENV", "development").lower() == "production"
    response.delete_cookie(key=COOKIE_NAME, path="/", secure=secure)


def safe_next_path(path: str | None) -> str:
    if not path or not path.startswith("/") or path.startswith("//"):
        return "/mlb/board"
    if path.startswith("/login"):
        return "/mlb/board"
    return path


def is_protected_path(path: str) -> bool:
    if path in PROTECTED_PAGE_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in PROTECTED_API_PREFIXES)


class AdminAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable):
        if not auth_enabled():
            return await call_next(request)

        path = request.url.path
        if path in {"/login", "/health"} or path.startswith("/api/auth/"):
            return await call_next(request)

        if not is_protected_path(path):
            return await call_next(request)

        if is_authenticated(request):
            return await call_next(request)

        if path.startswith("/api/"):
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required"},
            )

        next_path = path
        if request.url.query:
            next_path = f"{path}?{request.url.query}"
        from urllib.parse import quote

        return RedirectResponse(url=f"/login?next={quote(next_path)}", status_code=302)
