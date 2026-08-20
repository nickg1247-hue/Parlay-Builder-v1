"""File-flag maintenance mode with a temporary /test preview cookie.

Production is FastAPI behind nginx (not Apache/.htaccess). The on/off switch is
the presence of `maintenance.on` in the project root (or static/). Creating or
deleting that file takes effect on the next request — no rebuild or restart.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from fastapi import Request
from fastapi.responses import RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.config import PROJECT_ROOT

PREVIEW_COOKIE_NAME = "ntg_preview"
PREVIEW_COOKIE_VALUE = "1"
PREVIEW_MAX_AGE_SECONDS = 86400
CONSTRUCTION_PATH = "/under-construction.html"
FLAG_FILENAME = "maintenance.on"

_REDIRECT_HEADERS = {"Cache-Control": "no-store, no-cache, must-revalidate"}


def _cookie_secure() -> bool:
    raw = os.getenv("ADMIN_COOKIE_SECURE", "").strip().lower()
    if raw in ("1", "true", "yes"):
        return True
    if raw in ("0", "false", "no"):
        return False
    return os.getenv("APP_ENV", "development").lower() == "production"


def maintenance_flag_paths() -> list[Path]:
    """Locations Hostinger File Manager or the CLI can toggle without a deploy."""
    explicit = os.getenv("MAINTENANCE_FLAG_PATH", "").strip()
    if explicit:
        return [Path(explicit)]
    return [
        PROJECT_ROOT / FLAG_FILENAME,
        PROJECT_ROOT / "static" / FLAG_FILENAME,
    ]


def primary_flag_path() -> Path:
    return maintenance_flag_paths()[0]


def maintenance_enabled() -> bool:
    return any(path.is_file() for path in maintenance_flag_paths())


def turn_maintenance_on() -> Path:
    path = primary_flag_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("ON\n", encoding="utf-8")
    return path


def turn_maintenance_off() -> list[Path]:
    removed: list[Path] = []
    for path in maintenance_flag_paths():
        if path.is_file():
            path.unlink()
            removed.append(path)
    return removed


def has_preview_cookie(request: Request) -> bool:
    value = (request.cookies.get(PREVIEW_COOKIE_NAME) or "").strip()
    return value == PREVIEW_COOKIE_VALUE


def set_preview_cookie(response: Response) -> None:
    response.set_cookie(
        key=PREVIEW_COOKIE_NAME,
        value=PREVIEW_COOKIE_VALUE,
        max_age=PREVIEW_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(),
        path="/",
    )


def clear_preview_cookie(response: Response) -> None:
    response.delete_cookie(
        key=PREVIEW_COOKIE_NAME,
        path="/",
        secure=_cookie_secure(),
        httponly=True,
        samesite="lax",
    )


def is_maintenance_bypass_path(path: str) -> bool:
    """Allow construction page, /test, APIs, health, and non-HTML static assets."""
    if path == CONSTRUCTION_PATH:
        return True
    if path == "/test" or path.startswith("/test/"):
        return True
    if path.startswith("/api/"):
        return True
    if path in {"/health", "/sw.js", "/manifest.webmanifest"}:
        return True
    if path.startswith("/static/"):
        lowered = path.lower()
        return not (lowered.endswith(".html") or lowered.endswith(".htm"))
    return False


def construction_redirect() -> RedirectResponse:
    response = RedirectResponse(url=CONSTRUCTION_PATH, status_code=302)
    response.headers.update(_REDIRECT_HEADERS)
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


class MaintenanceModeMiddleware(BaseHTTPMiddleware):
    """When maintenance.on exists, send public HTML traffic to the construction page.

    Does not grant admin access, skip user auth, or alter API authorization.
    /test only sets a preview cookie so that browser can use the real frontend.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable):
        if not maintenance_enabled():
            return await call_next(request)

        path = request.url.path
        if is_maintenance_bypass_path(path) or has_preview_cookie(request):
            return await call_next(request)

        return construction_redirect()
