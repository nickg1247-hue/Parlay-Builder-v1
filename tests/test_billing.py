"""Stripe billing webhook and checkout helpers."""

import json
import uuid
from unittest.mock import patch

import pytest

from app.db.database import get_connection
from app.db.user_schema import ensure_users_table
from app.services.billing import handle_stripe_webhook
from app.services.user_accounts import create_user, mark_email_verified, set_stripe_customer_id


@pytest.fixture
def isolated_billing_db(tmp_path, monkeypatch):
    db_path = tmp_path / "billing.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    conn = get_connection()
    try:
        ensure_users_table(conn)
    finally:
        conn.close()
    yield db_path


def _subscription_event(event_id: str, customer_id: str, user_id: int) -> dict:
    return {
        "id": event_id,
        "type": "customer.subscription.updated",
        "data": {
            "object": {
                "customer": customer_id,
                "status": "trialing",
                "current_period_end": 1893456000,
                "metadata": {"user_id": str(user_id)},
            }
        },
    }


def test_webhook_idempotent(isolated_billing_db):
    email = f"billing-{uuid.uuid4().hex[:8]}@example.com"
    user, _ = create_user(email, "secretpass")
    mark_email_verified(user["id"])
    customer_id = "cus_test123"
    set_stripe_customer_id(user["id"], customer_id)

    event = _subscription_event("evt_test_1", customer_id, user["id"])
    payload = json.dumps(event).encode()

    with patch("app.services.billing.stripe.Webhook.construct_event", return_value=event):
        first = handle_stripe_webhook(payload, "sig")
        second = handle_stripe_webhook(payload, "sig")

    assert first == {"ok": True, "type": "customer.subscription.updated"}
    assert second == {"ok": True, "duplicate": True}

    from app.services.user_accounts import get_user_by_id

    updated = get_user_by_id(user["id"])
    assert updated["subscription_status"] == "trialing"
    assert updated["subscription_current_period_end"] is not None


def test_webhook_rejects_bad_signature(isolated_billing_db):
    with patch(
        "app.services.billing.stripe.Webhook.construct_event",
        side_effect=ValueError("Invalid signature"),
    ):
        with pytest.raises(ValueError):
            handle_stripe_webhook(b"{}", "bad_sig")


def test_billing_enabled_requires_keys(monkeypatch):
    from app.services.billing import billing_enabled

    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("STRIPE_PRICE_ID", raising=False)
    assert billing_enabled() is False

    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_x")
    assert billing_enabled() is True
