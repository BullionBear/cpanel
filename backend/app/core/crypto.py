"""Envelope encryption for credential secret material.

Each secret gets its own random data key: the plaintext is encrypted with
the data key, and the data key is encrypted with the master key from
configuration. Master key rotation therefore only requires re-wrapping the
small data keys, never re-encrypting payloads.

The master key never leaves this module, and decrypted plaintext must only
be requested at the moment it is used (e.g. request signing in a connector).
"""
import base64
import json

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


class EncryptionError(Exception):
    pass


class EncryptionNotConfigured(EncryptionError):
    pass


class DecryptionFailed(EncryptionError):
    pass


def generate_master_key() -> str:
    """Generate a value suitable for the CREDENTIAL_ENCRYPTION_KEY setting."""
    return Fernet.generate_key().decode()


class SecretBox:
    """Envelope encrypt/decrypt small secrets (API keys, private keys)."""

    def __init__(self, master_key: str | None = None):
        key = master_key if master_key is not None else settings.CREDENTIAL_ENCRYPTION_KEY
        if not key:
            raise EncryptionNotConfigured(
                "CREDENTIAL_ENCRYPTION_KEY is not set; generate one with "
                "app.core.crypto.generate_master_key()"
            )
        try:
            self._master = Fernet(key.encode())
        except (ValueError, TypeError) as exc:
            raise EncryptionError(f"Invalid master key: {exc}") from exc

    def encrypt(self, plaintext: str) -> str:
        """Return an opaque envelope string safe to store in the database."""
        data_key = Fernet.generate_key()
        ciphertext = Fernet(data_key).encrypt(plaintext.encode())
        wrapped_key = self._master.encrypt(data_key)
        envelope = {
            "v": 1,
            "k": base64.b64encode(wrapped_key).decode(),
            "c": base64.b64encode(ciphertext).decode(),
        }
        return json.dumps(envelope, separators=(",", ":"))

    def decrypt(self, envelope_str: str) -> str:
        try:
            envelope = json.loads(envelope_str)
            wrapped_key = base64.b64decode(envelope["k"])
            ciphertext = base64.b64decode(envelope["c"])
            data_key = self._master.decrypt(wrapped_key)
            return Fernet(data_key).decrypt(ciphertext).decode()
        except (InvalidToken, KeyError, ValueError, json.JSONDecodeError) as exc:
            raise DecryptionFailed("Cannot decrypt secret envelope") from exc

    def rotate_master(self, envelope_str: str, new_box: "SecretBox") -> str:
        """Re-wrap the data key under a new master key without touching the payload."""
        try:
            envelope = json.loads(envelope_str)
            wrapped_key = base64.b64decode(envelope["k"])
            data_key = self._master.decrypt(wrapped_key)
        except (InvalidToken, KeyError, ValueError, json.JSONDecodeError) as exc:
            raise DecryptionFailed("Cannot unwrap data key for rotation") from exc
        envelope["k"] = base64.b64encode(new_box._master.encrypt(data_key)).decode()
        return json.dumps(envelope, separators=(",", ":"))
