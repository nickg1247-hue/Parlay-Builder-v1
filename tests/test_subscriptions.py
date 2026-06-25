"""Subscription status helpers."""

from datetime import datetime, timedelta, timezone

from app.services.subscriptions import is_premium_user, subscription_period_active


def test_premium_active_subscription():
    end = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    user = {"subscription_status": "active", "subscription_current_period_end": end}
    assert is_premium_user(user) is True
    assert subscription_period_active(user) is True


def test_premium_expired_period():
    end = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    user = {"subscription_status": "active", "subscription_current_period_end": end}
    assert subscription_period_active(user) is False


def test_not_premium_canceled():
    user = {"subscription_status": "canceled", "subscription_current_period_end": None}
    assert is_premium_user(user) is False


def test_trialing_is_premium():
    end = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    user = {"subscription_status": "trialing", "subscription_current_period_end": end}
    assert is_premium_user(user) is True
