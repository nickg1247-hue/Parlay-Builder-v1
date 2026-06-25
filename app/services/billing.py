"""Stripe Checkout, Customer Portal, and subscription webhooks."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import stripe

from app.db.database import get_connection
from app.services.user_accounts import (
    get_user_by_id,
    get_user_by_stripe_customer_id,
    update_user_subscription,
    set_stripe_customer_id,
)

logger = logging.getLogger(__name__)

_CREATE_WEBHOOK_EVENTS = """
CREATE TABLE IF NOT EXISTS stripe_webhook_events (
    event_id TEXT PRIMARY KEY,
    processed_at TEXT NOT NULL
)
"""


def billing_enabled() -> bool:
    return bool(
        os.getenv("STRIPE_SECRET_KEY", "").strip()
        and os.getenv("STRIPE_PRICE_ID", "").strip()
    )


def public_site_url() -> str:
    return os.getenv("PUBLIC_SITE_URL", "http://127.0.0.1:8000").rstrip("/")


def stripe_trial_days() -> int:
    raw = os.getenv("STRIPE_TRIAL_DAYS", "7").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 7


def _stripe() -> stripe:
    key = os.getenv("STRIPE_SECRET_KEY", "").strip()
    if not key:
        raise RuntimeError("STRIPE_SECRET_KEY is not configured")
    stripe.api_key = key
    return stripe


def _ensure_webhook_table(conn) -> None:
    conn.execute(_CREATE_WEBHOOK_EVENTS)


def _period_end_iso(subscription: dict[str, Any]) -> str | None:
    end = subscription.get("current_period_end")
    if end is None:
        return None
    return datetime.fromtimestamp(int(end), tz=timezone.utc).isoformat()


def create_checkout_session(user: dict[str, Any]) -> str:
    if not user.get("email_verified_at"):
        raise ValueError("email_not_verified")
    price_id = os.getenv("STRIPE_PRICE_ID", "").strip()
    if not price_id:
        raise RuntimeError("STRIPE_PRICE_ID is not configured")

    client = _stripe()
    customer_id = user.get("stripe_customer_id")
    if not customer_id:
        customer = client.Customer.create(
            email=user["email"],
            metadata={"user_id": str(user["id"])},
        )
        customer_id = customer["id"]
        set_stripe_customer_id(user["id"], customer_id)

    trial_days = stripe_trial_days()
    sub_data: dict[str, Any] = {"metadata": {"user_id": str(user["id"])}}
    if trial_days > 0:
        sub_data["trial_period_days"] = trial_days

    session = client.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=f"{public_site_url()}/pricing?checkout=success",
        cancel_url=f"{public_site_url()}/pricing?checkout=canceled",
        subscription_data=sub_data,
        client_reference_id=str(user["id"]),
        metadata={"user_id": str(user["id"])},
    )
    url = session.get("url")
    if not url:
        raise RuntimeError("Stripe checkout session missing url")
    return url


def create_portal_session(user: dict[str, Any]) -> str:
    customer_id = user.get("stripe_customer_id")
    if not customer_id:
        raise ValueError("no_stripe_customer")
    session = _stripe().billing_portal.Session.create(
        customer=customer_id,
        return_url=f"{public_site_url()}/pricing",
    )
    url = session.get("url")
    if not url:
        raise RuntimeError("Stripe portal session missing url")
    return url


def _apply_subscription(subscription: dict[str, Any]) -> None:
    customer_id = subscription.get("customer")
    if not customer_id:
        return
    user = get_user_by_stripe_customer_id(str(customer_id))
    if not user:
        meta = subscription.get("metadata") or {}
        user_id = meta.get("user_id")
        if user_id:
            user = get_user_by_id(int(user_id))
            if user and not user.get("stripe_customer_id"):
                set_stripe_customer_id(user["id"], str(customer_id))
    if not user:
        logger.warning("Stripe subscription update for unknown customer %s", customer_id)
        return

    status = str(subscription.get("status") or "none").lower()
    update_user_subscription(
        user["id"],
        subscription_status=status,
        subscription_current_period_end=_period_end_iso(subscription),
    )


def _webhook_seen(event_id: str) -> bool:
    conn = get_connection()
    try:
        _ensure_webhook_table(conn)
        row = conn.execute(
            "SELECT 1 FROM stripe_webhook_events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _mark_webhook_processed(event_id: str) -> None:
    conn = get_connection()
    try:
        _ensure_webhook_table(conn)
        conn.execute(
            "INSERT OR IGNORE INTO stripe_webhook_events (event_id, processed_at) VALUES (?, ?)",
            (event_id, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def handle_stripe_webhook(payload: bytes, signature: str) -> dict[str, Any]:
    secret = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
    if not secret:
        raise RuntimeError("STRIPE_WEBHOOK_SECRET is not configured")

    event = stripe.Webhook.construct_event(payload, signature, secret)
    event_id = event.get("id")
    if not event_id:
        raise ValueError("missing_event_id")
    if _webhook_seen(event_id):
        return {"ok": True, "duplicate": True}

    event_type = event.get("type", "")
    data_object = (event.get("data") or {}).get("object") or {}

    if event_type in (
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    ):
        _apply_subscription(data_object)
    elif event_type == "checkout.session.completed":
        sub_id = data_object.get("subscription")
        if sub_id:
            subscription = _stripe().Subscription.retrieve(sub_id)
            _apply_subscription(subscription)

    _mark_webhook_processed(event_id)
    return {"ok": True, "type": event_type}
