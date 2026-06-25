"""End-user session auth (email signup) for player props access."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import time
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.auth.admin_auth import is_authenticated as is_admin_authenticated

USER_COOKIE_NAME = "ntg_user"
USER_SESSION_MAX_AGE_SECONDS = 30 * 24 * 3600
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

PROPS_API_EXACT = frozenset({
    "/api/daily/props",
    "/api/props/search",
    "/api/props/bookmakers",
    "/api/props/markets",
    "/api/props/cache-meta",
    "/api/parlay/props/eval",
    "/api/parlay/props/build",
    "/api/parlay/props/optimize",
    "/api/props/slip/export",
    "/api/props/tracker/summary",
    "/api/props/tracker/backfill",
})

PROPS_PAGE_PATHS = frozenset({
    "/mlb/props",
})

USER_AUTH_PUBLIC_PATHS = frozenset({
    "/signin",
    "/signup",
    "/verify-email",
    "/my-team",
    "/login",
    "/health",
    "/pricing",
})


def props_require_verified_user() -> bool:
    """When true, player props require a signed-in user account (soft gate — site stays public)."""
    explicit = os.getenv("PROPS_REQUIRE_VERIFIED_USER", "").strip().lower()
    return explicit in ("1", "true", "yes", "on")


def user_registration_enabled() -> bool:
    raw = os.getenv("USER_REGISTRATION_ENABLED", "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    return True


def normalize_email(email: str) -> str:
    return email.strip().lower()


def is_valid_email(email: str) -> bool:
    return bool(EMAIL_RE.match(normalize_email(email)))


def _user_session_secret() -> bytes:
    explicit = os.getenv("USER_SESSION_SECRET", "").strip()
    if explicit:
        return explicit.encode()
    admin = os.getenv("ADMIN_SESSION_SECRET", "").strip()
    if admin:
        return admin.encode()
    password = os.getenv("ADMIN_PASSWORD", "")
    return hashlib.sha256(f"ntg-user-session:{password}".encode()).digest()


def _user_cookie_secure() -> bool:
    raw = os.getenv("USER_COOKIE_SECURE", os.getenv("ADMIN_COOKIE_SECURE", "")).strip().lower()
    if raw in ("1", "true", "yes"):
        return True
    if raw in ("0", "false", "no"):
        return False
    return os.getenv("APP_ENV", "development").lower() == "production"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    iterations = 260_000
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    )
    return f"pbkdf2_sha256${iterations}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iter_str, salt, digest_hex = stored.split("$", 3)
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    try:
        iterations = int(iter_str)
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    )
    return secrets.compare_digest(digest.hex(), digest_hex)


def _sign_user_payload(payload: str) -> str:
    sig = hmac.new(_user_session_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def create_user_session_token(user_id: int, email: str) -> str:
    exp = int(time.time()) + USER_SESSION_MAX_AGE_SECONDS
    email_key = normalize_email(email)
    return _sign_user_payload(f"{user_id}|{email_key}|{exp}")


def _verify_user_token(token: str) -> tuple[int, str] | None:
    if not token or "." not in token:
        return None
    payload, sig = token.rsplit(".", 1)
    expected = hmac.new(_user_session_secret(), payload.encode(), hashlib.sha256).hexdigest()
    if not secrets.compare_digest(sig, expected):
        return None
    parts = payload.split("|")
    if len(parts) != 3:
        return None
    user_id_str, email, exp_str = parts
    try:
        user_id = int(user_id_str)
        exp = int(exp_str)
    except ValueError:
        return None
    if exp < int(time.time()):
        return None
    return user_id, email


def get_user_session(request: Request) -> dict[str, Any] | None:
    token = request.cookies.get(USER_COOKIE_NAME, "")
    parsed = _verify_user_token(token)
    if not parsed:
        return None
    user_id, email = parsed
    return {"user_id": user_id, "email": email}


def set_user_session_cookie(response: Response, user_id: int, email: str) -> None:
    secure = _user_cookie_secure()
    response.set_cookie(
        key=USER_COOKIE_NAME,
        value=create_user_session_token(user_id, email),
        max_age=USER_SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def clear_user_session_cookie(response: Response) -> None:
    secure = _user_cookie_secure()
    response.delete_cookie(key=USER_COOKIE_NAME, path="/", secure=secure)


def safe_user_next_path(path: str | None) -> str:
    if not path or not path.startswith("/") or path.startswith("//"):
        return "/"
    if path.startswith(("/signin", "/signup", "/login", "/verify-email")):
        return "/"
    return path


def is_props_api_path(path: str) -> bool:
    if path in PROPS_API_EXACT:
        return True
    if path.startswith("/api/games/mlb/") and path.endswith("/props"):
        return True
    if path.startswith("/api/parlay/props/"):
        return True
    return False


def is_props_page_path(path: str) -> bool:
    return path in PROPS_PAGE_PATHS


def can_access_props(request: Request, *, user_row: dict[str, Any] | None = None) -> bool:
    if not props_require_verified_user():
        return True
    if is_admin_authenticated(request):
        return True
    session = get_user_session(request)
    if not session:
        return False
    if user_row is None:
        from app.services.user_accounts import get_user_by_id

        user_row = get_user_by_id(session["user_id"])
    return user_row is not None


class UserPropsAuthMiddleware(BaseHTTPMiddleware):
    """Soft gate: props require signed-in user; games/news stay public."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        if not props_require_verified_user():
            return await call_next(request)

        path = request.url.path
        if path in USER_AUTH_PUBLIC_PATHS or path.startswith("/api/auth/user/"):
            return await call_next(request)
        if path.startswith("/static/") or path == "/api/build" or path == "/api/auth/status":
            return await call_next(request)
        # Player lookup / stats modals stay public (props list may still require sign-in).
        if path.startswith("/api/players/"):
            return await call_next(request)

        needs_props = is_props_api_path(path) or is_props_page_path(path)
        if not needs_props:
            return await call_next(request)

        if can_access_props(request):
            return await call_next(request)

        if path.startswith("/api/"):
            return JSONResponse(
                status_code=401,
                content={
                    "detail": "Sign in to view player props.",
                    "code": "props_auth_required",
                },
            )

        from urllib.parse import quote

        next_path = path
        if request.url.query:
            next_path = f"{path}?{request.url.query}"
        return RedirectResponse(url=f"/signin?next={quote(next_path)}", status_code=302)
