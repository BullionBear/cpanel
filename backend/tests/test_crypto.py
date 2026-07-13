import pytest

from app.core.crypto import (
    DecryptionFailed,
    EncryptionNotConfigured,
    SecretBox,
    generate_master_key,
)


def test_roundtrip():
    box = SecretBox(generate_master_key())
    envelope = box.encrypt("super-secret-api-key")
    assert "super-secret-api-key" not in envelope
    assert box.decrypt(envelope) == "super-secret-api-key"


def test_each_secret_gets_unique_envelope():
    box = SecretBox(generate_master_key())
    assert box.encrypt("same") != box.encrypt("same")


def test_wrong_master_key_fails():
    envelope = SecretBox(generate_master_key()).encrypt("secret")
    other = SecretBox(generate_master_key())
    with pytest.raises(DecryptionFailed):
        other.decrypt(envelope)


def test_tampered_envelope_fails():
    import base64
    import json

    box = SecretBox(generate_master_key())
    envelope = json.loads(box.encrypt("secret"))

    # Flip a byte in the middle of the ciphertext token (breaks the HMAC)
    token = bytearray(base64.b64decode(envelope["c"]))
    token[len(token) // 2] ^= 0xFF
    tampered = dict(envelope, c=base64.b64encode(bytes(token)).decode())
    with pytest.raises(DecryptionFailed):
        box.decrypt(json.dumps(tampered))

    with pytest.raises(DecryptionFailed):
        box.decrypt("not-json")


def test_master_key_rotation():
    old_box = SecretBox(generate_master_key())
    new_box = SecretBox(generate_master_key())
    envelope = old_box.encrypt("secret")

    rotated = old_box.rotate_master(envelope, new_box)
    assert new_box.decrypt(rotated) == "secret"
    with pytest.raises(DecryptionFailed):
        old_box.decrypt(rotated)


def test_unconfigured_key_raises(monkeypatch):
    from app.core import crypto

    monkeypatch.setattr(crypto.settings, "CREDENTIAL_ENCRYPTION_KEY", "")
    with pytest.raises(EncryptionNotConfigured):
        SecretBox()
