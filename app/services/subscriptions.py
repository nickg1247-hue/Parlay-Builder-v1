"""Premium subscription status for end-user accounts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import Request

from app.auth.admin_auth import is_authenticated as is_admin_authenticated
from app.auth.user_auth import get_user_session

PREMIUM_STATUSES = frozenset({"active", "trialing"})


def _parse_iso_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def subscription_period_active(user: dict[str, Any] | None) -> bool:
    if not user:
        return False
    end = _parse_iso_dt(user.get("subscription_current_period_end"))
    if end is None:
        return user.get("subscription_status") in PREMIUM_STATUSES
    return end >= datetime.now(timezone.utc)


def is_premium_user(
    user: dict[str, Any] | None,
    request: Request | None = None,
) -> bool:
    if request is not None and is_admin_authenticated(request):
        return True
    if not user:
        return False
    status = (user.get("subscription_status") or "none").lower()
    if status not in PREMIUM_STATUSES:
        return False
    return subscription_period_active(user)


def subscription_summary(user: dict[str, Any] | None) -> dict[str, Any]:
    if not user:
        return {
            "subscription_status": "none",
            "is_premium": False,
            "subscription_current_period_end": None,
        }
    premium = is_premium_user(user)
    return {
        "subscription_status": user.get("subscription_status") or "none",
        "is_premium": premium,
        "subscription_current_period_end": user.get("subscription_current_period_end"),
        "stripe_customer_id": user.get("stripe_customer_id"),
    }


def is_premium(
    user: dict[str, Any] | None,
    request: Request | None = None,
) -> bool:
    """Whether the user has an active paid or trialing subscription."""
    return is_premium_user(user, request)


def resolve_user_from_request(request: Request) -> dict[str, Any] | None:
    session = get_user_session(request)
    if not session:
        return None
    from app.services.user_accounts import get_user_by_id

    return get_user_by_id(session["user_id"])
