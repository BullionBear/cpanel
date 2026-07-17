"""Human-friendly explanations for credential-verification failures.

Verifying an API key can fail for very different reasons: a malformed secret
that never leaves the process, a network hiccup, or an actual rejection by the
venue. Collapsing them all into one opaque message ("the venue rejected this
API key") makes support impossible — the operator can't tell a mistyped key
from a real permission problem.

``credential_error_reason`` maps an exception to a short explanation. It is
built only from the exception type, the HTTP status, and venue-provided error
text — never the secret or the signed request — so the result is safe to
return to the client and to log.
"""
from __future__ import annotations

# Substrings that mark a local key-parsing / signing failure (the request
# never reached the venue). Covers PyCryptodome and cryptography messages.
_KEY_HINTS = (
    "pem",
    "private key",
    "public key",
    "key format",
    "der",
    "asn.1",
    "ecc",
    "rsa key",
    "at least 3 lines",
)

_NETWORK_HINTS = (
    "timeout",
    "timed out",
    "connection",
    "network",
    "unreachable",
    "getaddrinfo",
    "temporarily unavailable",
)


def _status_code(exc: Exception) -> int | None:
    # Binance exposes status_code; gate-api and generated OpenAPI clients
    # expose status.
    for attr in ("status_code", "status"):
        code = getattr(exc, attr, None)
        if isinstance(code, int):
            return code
    return None


def _venue_message(exc: Exception) -> str | None:
    """Venue-supplied error text, if the SDK exposes it as a clean attribute.

    Binance errors carry ``error_message``; pybit carries ``message``. We only
    read these named attributes (not ``str(exc)``) so the signed request — which
    some SDKs embed in the stringified exception — never leaks out.
    """
    for attr in ("error_message", "message"):
        val = getattr(exc, attr, None)
        if isinstance(val, str) and val.strip():
            # Trim trailing punctuation so callers can append their own.
            return val.strip().rstrip(".").strip()
    return None


def credential_error_reason(exchange: str, exc: Exception) -> str:
    """A short, secret-free explanation of why verification failed."""
    text = str(exc).lower()
    status = _status_code(exc)
    message = _venue_message(exc)

    # Local (pre-flight) failure: the secret / private key could not be parsed
    # or used to sign, so the request never reached the venue. Only trust the
    # textual hints when there is no HTTP status — a venue response means the
    # request was signed and sent, and its stringified body/headers can
    # coincidentally contain hint substrings (e.g. "der" in "HTTPHeaderDict").
    if isinstance(exc, (ValueError, TypeError)) or (
        status is None and any(h in text for h in _KEY_HINTS)
    ):
        return (
            "The API secret or private key is malformed. Check that the scheme "
            "matches the key and that the full key (including PEM header/footer) "
            "was pasted correctly."
        )

    # No HTTP status and a connectivity-shaped message: we never got a response.
    if status is None and any(w in text for w in _NETWORK_HINTS):
        return f"Could not reach {exchange}. Check network connectivity and try again."

    if status in (401, 403):
        detail = f": {message}" if message else ""
        return (
            f"{exchange} rejected the key (HTTP {status}){detail}. "
            "Verify the key/secret, its permissions, and any IP allowlist."
        )

    if message:
        prefix = f"{exchange} rejected the key"
        if status:
            prefix += f" (HTTP {status})"
        return f"{prefix}: {message}"

    return f"{exchange} rejected this API key."
