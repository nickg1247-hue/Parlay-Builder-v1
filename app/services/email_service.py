"""Transactional email for account verification."""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)


def _smtp_configured() -> bool:
    return bool(os.getenv("SMTP_HOST", "").strip())


def public_site_url() -> str:
    return os.getenv("PUBLIC_SITE_URL", "http://127.0.0.1:8000").rstrip("/")


def build_verification_url(token: str) -> str:
    return f"{public_site_url()}/verify-email?token={token}"


def send_verification_email(to_email: str, token: str) -> bool:
    """Send verification email. Returns True if sent (or logged in dev fallback)."""
    verify_url = build_verification_url(token)
    subject = "Verify your NTG Sports account"
    body = (
        "Thanks for signing up for NTG Sports.\n\n"
        "Verify your email to unlock player props:\n"
        f"{verify_url}\n\n"
        "This link expires in 24 hours.\n"
        "If you did not create an account, you can ignore this email."
    )

    if not _smtp_configured():
        logger.warning(
            "SMTP not configured — verification link for %s: %s",
            to_email,
            verify_url,
        )
        return False

    host = os.getenv("SMTP_HOST", "").strip()
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").strip()
    from_addr = os.getenv("EMAIL_FROM", user or "noreply@ntgsports.com").strip()
    use_tls = os.getenv("SMTP_USE_TLS", "true").strip().lower() in ("1", "true", "yes")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_email
    msg.set_content(body)

    try:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            if use_tls:
                smtp.starttls()
            if user and password:
                smtp.login(user, password)
            smtp.send_message(msg)
        return True
    except Exception as exc:
        logger.error("Failed to send verification email to %s: %s", to_email, exc)
        return False
