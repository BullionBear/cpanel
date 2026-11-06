"""Tests for credential-verification error classification.

These lock in that a failed verification produces a specific, secret-free
reason rather than the old opaque "the venue rejected this API key" for every
cause (malformed key, network error, real venue rejection)."""
from app.connectors.errors import credential_error_reason


class _VenueError(Exception):
    """Mimics the SDK error shape (Binance error_message/status_code)."""

    def __init__(self, error_message=None, status_code=None):
        self.error_message = error_message
        self.status_code = status_code
        super().__init__(status_code, error_message)


def test_malformed_pem_is_reported_as_local_key_error():
    exc = ValueError("Unsupported or invalid private key format. Private key must be ED25519")
    reason = credential_error_reason("BINANCE", exc)
    assert "malformed" in reason.lower()
    # The request never reached the venue, so we must not blame it.
    assert "rejected" not in reason.lower()


def test_pem_line_error_classified_locally():
    reason = credential_error_reason("BINANCE", ValueError("A PEM file must have at least 3 lines"))
    assert "malformed" in reason.lower()


def test_unauthorized_surfaces_status_and_venue_message():
    exc = _VenueError(error_message="Invalid API-key, IP, or permissions for action.", status_code=401)
    reason = credential_error_reason("BINANCE", exc)
    assert "401" in reason
    assert "Invalid API-key" in reason
    assert "allowlist" in reason.lower()


def test_forbidden_status_is_surfaced():
    reason = credential_error_reason("BYBIT", _VenueError(error_message="IP not allowed", status_code=403))
    assert "403" in reason
    assert "IP not allowed" in reason


def test_generic_venue_message_without_status():
    exc = Exception("something odd")
    exc.message = "api key expired"  # pybit-style attribute
    reason = credential_error_reason("BYBIT", exc)
    assert "BYBIT rejected the key" in reason
    assert "api key expired" in reason


def test_network_error_without_status():
    exc = Exception("Connection to api.binance.com timed out")
    reason = credential_error_reason("BINANCE", exc)
    assert "Could not reach BINANCE" in reason


def test_fallback_when_nothing_known():
    reason = credential_error_reason("BINANCE", Exception(""))
    assert reason == "BINANCE rejected this API key."


def test_gate_style_status_attribute_is_recognized():
    # gate-api (generated OpenAPI client) exposes .status, not .status_code.
    exc = Exception("boom")
    exc.status = 403
    exc.message = "Request API key does not have account permission"
    reason = credential_error_reason("GATEIO", exc)
    assert "403" in reason
    assert "account permission" in reason


def test_venue_response_with_key_hint_text_is_not_local_error():
    # gate-api's str(exc) includes headers like "HTTPHeaderDict" ("der" hint);
    # a response with an HTTP status must never be classified as a local
    # malformed-key failure.
    exc = Exception("HTTP response headers: HTTPHeaderDict({'Server': 'openresty'})")
    exc.status = 401
    exc.message = "Invalid key"
    reason = credential_error_reason("GATEIO", exc)
    assert "malformed" not in reason.lower()
    assert "401" in reason


def test_reason_never_includes_stringified_exception_for_venue_errors():
    # pybit embeds the signed request in str(exc); ensure we only use .message.
    exc = Exception("GET /v5/user/query-api?api_key=AKX&sign=LEAKEDSIGNATURE")
    reason = credential_error_reason("BYBIT", exc)
    assert "LEAKEDSIGNATURE" not in reason
    assert reason == "BYBIT rejected this API key."
