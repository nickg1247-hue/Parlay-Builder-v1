"""Block expensive odds/props queries from non-admin callers."""

from __future__ import annotations

from fastapi import HTTPException, Request

from app.auth.admin_auth import is_authenticated as is_admin_authenticated
from app.services.subscriptions import is_premium_user, resolve_user_from_request


def assert_expensive_query_allowed(
    request: Request,
    *,
    refresh: bool = False,
    scan: bool = False,
    live_test: bool = False,
) -> None:
    """Only admin (or server cron with admin cookie) may force live API pulls."""
    if not (refresh or scan or live_test):
        return
    if is_admin_authenticated(request):
        return
    user = resolve_user_from_request(request)
    if user and is_premium_user(user, request):
        # Premium users still cannot force refresh — protects Odds API quota.
        pass
    raise HTTPException(
        status_code=403,
        detail="Live refresh is not available. Picks update on the morning schedule.",
    )
