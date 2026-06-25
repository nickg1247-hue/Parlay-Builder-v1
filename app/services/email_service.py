"""Transactional email for account verification."""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)


def _smtp_configured() -> bool:
    return bool(os.getenv("SMTP_HOST", "").strip())


def dev_expose_verification_url() -> bool:
    """In local dev without SMTP, return the verify link in API responses."""
    if os.getenv("APP_ENV", "development").strip().lower() != "development":
        return False
    return not _smtp_configured()


def public_site_url() -> str:
    return os.getenv("PUBLIC_SITE_URL", "http://127.0.0.1:8000").rstrip("/")


def build_verification_url(token: str) -> str:
    return f"{public_site_url()}/verify-email?token={token}"


def _smtp_password() -> str:
    return os.getenv("SMTP_PASSWORD", "").strip().replace(" ", "")


def smtp_config_summary() -> dict[str, str | bool]:
    """Non-secret SMTP config for diagnostics."""
    user = os.getenv("SMTP_USER", "").strip()
    from_addr = os.getenv("EMAIL_FROM", user or "").strip()
    return {
        "configured": _smtp_configured(),
        "host": os.getenv("SMTP_HOST", "").strip(),
        "port": os.getenv("SMTP_PORT", "587").strip(),
        "user": user,
        "from_addr": from_addr,
        "use_tls": os.getenv("SMTP_USE_TLS", "true").strip().lower()
        in ("1", "true", "yes"),
        "user_looks_like_email": "@" in user,
        "from_matches_user": from_addr.lower() == user.lower() if user and from_addr else False,
    }


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
    password = _smtp_password()
    from_addr = os.getenv("EMAIL_FROM", user or "noreply@ntgsports.com").strip()
    use_tls = os.getenv("SMTP_USE_TLS", "true").strip().lower() in ("1", "true", "yes")

    if not user or "@" not in user:
        logger.error(
            "SMTP_USER must be a full email address (e.g. you@gmail.com), got: %r",
            user,
        )
        return False
    if "gmail.com" in host and from_addr.lower() != user.lower():
        logger.warning(
            "Gmail SMTP: EMAIL_FROM (%s) should match SMTP_USER (%s) on free Gmail",
            from_addr,
            user,
        )
        from_addr = user

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
        logger.info("Verification email sent to %s", to_email)
        return True
    except smtplib.SMTPAuthenticationError as exc:
        logger.error(
            "SMTP auth failed for %s — check SMTP_USER and SMTP_PASSWORD (Gmail app password): %s",
            user,
            exc,
        )
        return False
    except OSError as exc:
        logger.error(
            "SMTP connection failed to %s:%s — VPS may block outbound port %s: %s",
            host,
            port,
            port,
            exc,
        )
        return False
    except Exception as exc:
        logger.error("Failed to send verification email to %s: %s", to_email, exc)
        return False


def send_team_digest_email(to_email: str, subject: str, body: str) -> bool:
    """Daily followed-teams slate digest (MVP — same SMTP path as verification)."""
    if not _smtp_configured():
        logger.warning("SMTP not configured — digest for %s:\n%s", to_email, body[:500])
        return False
    host = os.getenv("SMTP_HOST", "").strip()
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER", "").strip()
    password = _smtp_password()
    from_addr = os.getenv("SMTP_FROM", user).strip()
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
        logger.info("Team digest sent to %s", to_email)
        return True
    except Exception as exc:
        logger.error("Failed to send team digest to %s: %s", to_email, exc)
        return False
