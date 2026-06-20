"""End-user account CRUD (SQLite)."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from app.auth.user_auth import hash_password, normalize_email, verify_password
from app.db.database import get_connection


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "id": row[0],
        "email": row[1],
        "password_hash": row[2],
        "email_verified_at": row[3],
        "verification_token": row[4],
        "verification_token_expires_at": row[5],
        "created_at": row[6],
    }


def _select_user(conn, where: str, param: Any) -> dict[str, Any] | None:
    cur = conn.execute(
        f"""
        SELECT id, email, password_hash, email_verified_at,
               verification_token, verification_token_expires_at, created_at
        FROM users
        WHERE {where}
        LIMIT 1
        """,
        (param,),
    )
    row = cur.fetchone()
    return _row_to_dict(row) if row else None


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        return _select_user(conn, "id = ?", user_id)
    finally:
        conn.close()


def get_user_by_email(email: str) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        return _select_user(conn, "email = ?", normalize_email(email))
    finally:
        conn.close()


def get_user_by_verification_token(token: str) -> dict[str, Any] | None:
    if not token:
        return None
    conn = get_connection()
    try:
        return _select_user(conn, "verification_token = ?", token)
    finally:
        conn.close()


def create_user(email: str, password: str) -> tuple[dict[str, Any], str]:
    """Create user; returns (user_row, verification_token)."""
    email_key = normalize_email(email)
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO users (
                email, password_hash, email_verified_at,
                verification_token, verification_token_expires_at, created_at
            ) VALUES (?, ?, NULL, ?, ?, ?)
            """,
            (email_key, hash_password(password), token, expires, _now_iso()),
        )
        conn.commit()
        user = get_user_by_email(email_key)
        if not user:
            raise RuntimeError("Failed to load user after insert")
        return user, token
    finally:
        conn.close()


def mark_email_verified(user_id: int) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE users
            SET email_verified_at = ?,
                verification_token = NULL,
                verification_token_expires_at = NULL
            WHERE id = ?
            """,
            (_now_iso(), user_id),
        )
        conn.commit()
    finally:
        conn.close()


def rotate_verification_token(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE users
            SET verification_token = ?, verification_token_expires_at = ?
            WHERE id = ?
            """,
            (token, expires, user_id),
        )
        conn.commit()
    finally:
        conn.close()
    return token


def verify_user_credentials(email: str, password: str) -> dict[str, Any] | None:
    user = get_user_by_email(email)
    if not user:
        return None
    if not verify_password(password, user["password_hash"]):
        return None
    return user


def verification_token_valid(user: dict[str, Any]) -> bool:
    if user.get("email_verified_at"):
        return False
    token = user.get("verification_token")
    expires = user.get("verification_token_expires_at")
    if not token or not expires:
        return False
    try:
        exp_dt = datetime.fromisoformat(str(expires))
    except ValueError:
        return False
    if exp_dt.tzinfo is None:
        exp_dt = exp_dt.replace(tzinfo=timezone.utc)
    return exp_dt >= datetime.now(timezone.utc)
