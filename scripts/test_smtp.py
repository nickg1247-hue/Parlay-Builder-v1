#!/usr/bin/env python3
"""Test SMTP settings from .env — run on the VPS after editing .env."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app.config  # noqa: F401 — load .env

from app.services.email_service import send_verification_email, smtp_config_summary


def main() -> int:
    to_email = sys.argv[1] if len(sys.argv) > 1 else None
    if not to_email:
        print("Usage: python scripts/test_smtp.py you@example.com")
        return 1

    summary = smtp_config_summary()
    print("SMTP config (no secrets):")
    for key, value in summary.items():
        print(f"  {key}: {value}")

    if not summary.get("user_looks_like_email"):
        print("\nFAIL: SMTP_USER must be a full email like you@gmail.com")
        return 1

    print(f"\nSending test verification email to {to_email} ...")
    ok = send_verification_email(to_email, "test-token-not-valid-for-verify")
    if ok:
        print("OK: email send reported success — check inbox and spam.")
        return 0

    print("FAIL: could not send — check logs above and: sudo journalctl -u parlay-builder -n 30")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
