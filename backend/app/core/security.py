"""Security utilities for authentication and password hashing."""
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against a hashed password."""
    try:
        return bcrypt.checkpw(
            plain_password.encode('utf-8'),
            hashed_password.encode('utf-8')
        )
    except Exception:
        return False


def get_password_hash(password: str) -> str:
    """Hash a password using bcrypt."""
    # Encode password to bytes
    password_bytes = password.encode('utf-8')
    # Generate salt and hash
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(password_bytes, salt)
    # Return as string
    return hashed.decode('utf-8')


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None, prefix: str = "") -> str:
    """Create a JWT access token with optional prefix.
    
    Args:
        data: Token payload data
        expires_delta: Optional expiration time delta
        prefix: Optional token prefix (e.g., 'ui_', 'api_')
    
    Returns:
        JWT token string with optional prefix
    """
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    # Add iat (issued at) with microseconds and a random nonce to ensure uniqueness
    now = datetime.now(timezone.utc)
    to_encode.update({
        "exp": expire,
        "iat": now.timestamp(),  # Include microseconds precision
        "jti": secrets.token_urlsafe(8),  # Unique token identifier to prevent collisions
    })
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return f"{prefix}{encoded_jwt}" if prefix else encoded_jwt


def decode_access_token(token: str) -> Optional[dict]:
    """Decode and verify a JWT access token.
    
    Supports tokens with prefixes (ui_, api_) by stripping the prefix before decoding.
    
    Args:
        token: JWT token string, optionally prefixed with 'ui_' or 'api_'
    
    Returns:
        Decoded token payload or None if invalid
    """
    # Strip prefix if present (ui_ or api_)
    actual_token = token
    if token.startswith("ui_"):
        actual_token = token[3:]
    elif token.startswith("api_"):
        actual_token = token[4:]
    
    try:
        payload = jwt.decode(actual_token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except JWTError:
        return None

