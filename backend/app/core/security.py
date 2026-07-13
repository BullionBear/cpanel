"""JWT helpers for the DB-less Discord OAuth flow.

The rebuilt backend has no users table yet, so the signed-in identity
(Discord id + display name) is embedded directly in the tokens.
"""
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt

from app.core.config import settings


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None, prefix: str = "") -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    now = datetime.now(timezone.utc)
    to_encode.update({
        "exp": expire,
        "iat": now.timestamp(),
        "jti": secrets.token_urlsafe(8),
    })
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return f"{prefix}{encoded_jwt}" if prefix else encoded_jwt


def decode_access_token(token: str) -> Optional[dict]:
    """Decode a JWT, stripping a ui_ / api_ prefix if present."""
    actual_token = token
    if token.startswith("ui_"):
        actual_token = token[3:]
    elif token.startswith("api_"):
        actual_token = token[4:]

    try:
        return jwt.decode(actual_token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        return None


def create_oauth_state_token() -> str:
    """Signed CSRF token for the Discord OAuth state parameter."""
    return create_access_token(
        data={"type": "oauth_state", "nonce": secrets.token_urlsafe(16)},
        expires_delta=timedelta(minutes=10),
    )


def verify_oauth_state_token(state: str) -> bool:
    payload = decode_access_token(state)
    return payload is not None and payload.get("type") == "oauth_state"


def create_discord_exchange_token(discord_id: str, username: str) -> str:
    """Short-lived one-time exchange code for the frontend callback."""
    return create_access_token(
        data={"sub": discord_id, "name": username, "type": "discord_exchange"},
        expires_delta=timedelta(seconds=60),
    )


# Track consumed exchange codes (single-use within process lifetime).
_used_discord_exchange_jtis: set[str] = set()


def consume_discord_exchange_token(code: str) -> Optional[dict]:
    """Return the embedded identity for a valid, unused exchange code."""
    payload = decode_access_token(code)
    if payload is None or payload.get("type") != "discord_exchange":
        return None
    jti = payload.get("jti")
    if not jti or not payload.get("sub") or jti in _used_discord_exchange_jtis:
        return None
    _used_discord_exchange_jtis.add(jti)
    return payload
