"""Accounts, password hashing and sessions.

FDSRouter has no accounts until someone creates the first one -- see schema.sql's comment on the
`user` table. Every request in that "bootstrap" state is served exactly as before this module
existed, so an existing single-user installation keeps working unchanged across an update. Once
one account exists, every /api/* route except /api/auth/* requires a valid session (see the
middleware in api/app.py).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from fdsrouter.db.database import Database

SESSION_TTL = timedelta(days=14)
# Only bump last_seen_at/expires_at this often -- a sliding expiry that rewrote the session row
# on every single request would turn "is my session valid" into a DB write per request.
SESSION_TOUCH_INTERVAL = timedelta(minutes=5)

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_DKLEN)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, n_str, r_str, p_str, salt_hex, hash_hex = stored.split("$")
        if algorithm != "scrypt":
            return False
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n_str),
            r=int(r_str),
            p=int(p_str),
            dklen=len(hash_hex) // 2,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(derived.hex(), hash_hex)


def is_bootstrap_mode(db: Database) -> bool:
    return db.count_users() == 0


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    # A session token is already 256 bits of CSPRNG output -- scrypt would only add CPU cost
    # for no security benefit, unlike a human-chosen password.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_session(db: Database, user_id: str) -> tuple[str, datetime]:
    token = generate_session_token()
    expires_at = _now() + SESSION_TTL
    db.create_session_row(hash_token(token), user_id, expires_at.isoformat())
    return token, expires_at


def resolve_session(db: Database, raw_token: str) -> dict[str, Any] | None:
    """Look up the session for a raw cookie value, sliding its expiry forward, and return the
    owning user -- or None if the token is missing, unknown, or expired."""
    token_hash = hash_token(raw_token)
    session = db.get_session(token_hash)
    if session is None:
        return None
    expires_at = datetime.fromisoformat(session["expires_at"])
    now = _now()
    if expires_at <= now:
        db.delete_session(token_hash)
        return None

    last_seen = datetime.fromisoformat(session["last_seen_at"]) if session["last_seen_at"] else None
    if last_seen is None or now - last_seen >= SESSION_TOUCH_INTERVAL:
        db.touch_session(token_hash, (now + SESSION_TTL).isoformat(), now.isoformat())

    return db.get_user(session["user_id"])


def resolve_or_create_proxy_user(db: Database, username: str) -> dict[str, Any]:
    """Auto-provision a passwordless account the first time a trusted reverse-proxy header names
    a username FDSRouter hasn't seen before. Only ever called when config.trusted_proxy_header is
    set -- trusting the header at all is an explicit installer decision, made once in
    config.yaml."""
    user = db.get_user_by_username(username)
    if user is not None:
        db.touch_last_login(user["id"])
        return user
    user = db.create_user(username=username, display_name=username, password_hash=None)
    db.touch_last_login(user["id"])
    return user
