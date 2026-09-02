"""Unit tests for shared cryptographic utilities."""

import pytest

from registry_pkgs.core.crypto_utils import (
    ENCRYPTED_VALUE_PATTERN,
    decrypt_auth_fields,
    decrypt_value,
    encrypt_auth_fields,
    encrypt_value,
    is_encrypted,
)

_KEY = bytes.fromhex("00" * 16)  # AES-128 test key


def test_encrypt_decrypt_round_trip():
    ciphertext = encrypt_value("s3cr3t-value", encryption_key=_KEY)
    assert ciphertext != "s3cr3t-value"
    assert is_encrypted(ciphertext)
    assert decrypt_value(ciphertext, encryption_key=_KEY) == "s3cr3t-value"


def test_encrypt_produces_random_iv_each_call():
    a = encrypt_value("same", encryption_key=_KEY)
    b = encrypt_value("same", encryption_key=_KEY)
    assert a != b  # random IV per encryption
    assert decrypt_value(a, encryption_key=_KEY) == decrypt_value(b, encryption_key=_KEY) == "same"


def test_empty_string_passes_through():
    assert encrypt_value("", encryption_key=_KEY) == ""
    assert decrypt_value("", encryption_key=_KEY) == ""


def test_non_encrypted_value_returned_as_is():
    # No colon separator → treated as already-plaintext (backward compatibility).
    assert decrypt_value("plaintext", encryption_key=_KEY) == "plaintext"


def test_is_encrypted_pattern():
    assert is_encrypted(f"{'00' * 16}:deadbeef")
    assert not is_encrypted("plaintext")
    assert not is_encrypted("")
    assert not is_encrypted("short:iv")  # IV not 32 hex chars
    assert ENCRYPTED_VALUE_PATTERN.match(f"{'00' * 16}:x")


def test_invalid_iv_length_raises():
    with pytest.raises(Exception, match="Failed to decrypt value"):
        decrypt_value(f"{'00' * 8}:deadbeef", encryption_key=_KEY)


class TestEncryptAuthFields:
    def test_encrypt_oauth_client_secret(self):
        config = {"oauth": {"client_id": "cid", "client_secret": "my_secret"}}
        result = encrypt_auth_fields(config, encryption_key=_KEY)
        assert is_encrypted(result["oauth"]["client_secret"])
        assert result["oauth"]["client_secret"] != "my_secret"
        assert result["oauth"]["client_id"] == "cid"

    def test_encrypt_oauth_already_encrypted_not_reencrypted(self):
        already = f"{'01' * 16}:encrypted_data"
        config = {"oauth": {"client_secret": already}}
        result = encrypt_auth_fields(config, encryption_key=_KEY)
        assert result["oauth"]["client_secret"] == already

    def test_encrypt_apikey_key(self):
        config = {"apiKey": {"key": "my_api_key", "authorization_type": "bearer"}}
        result = encrypt_auth_fields(config, encryption_key=_KEY)
        assert is_encrypted(result["apiKey"]["key"])
        assert result["apiKey"]["key"] != "my_api_key"

    def test_encrypt_both_oauth_and_apikey(self):
        config = {"oauth": {"client_secret": "oauth_secret"}, "apiKey": {"key": "api_key"}}
        result = encrypt_auth_fields(config, encryption_key=_KEY)
        assert is_encrypted(result["oauth"]["client_secret"])
        assert is_encrypted(result["apiKey"]["key"])

    def test_none_key_returns_unchanged(self):
        config = {"oauth": {"client_secret": "plaintext_secret"}}
        result = encrypt_auth_fields(config, encryption_key=None)
        assert result["oauth"]["client_secret"] == "plaintext_secret"

    def test_empty_config(self):
        assert encrypt_auth_fields({}, encryption_key=_KEY) == {}
        assert encrypt_auth_fields(None, encryption_key=_KEY) is None

    def test_oauth_without_client_secret(self):
        config = {"oauth": {"client_id": "cid"}}
        result = encrypt_auth_fields(config, encryption_key=_KEY)
        assert result["oauth"]["client_id"] == "cid"
        assert "client_secret" not in result["oauth"]


class TestDecryptAuthFields:
    def test_none_key_returns_unchanged(self):
        encrypted = f"{'0a' * 16}:encrypted"
        config = {"oauth": {"client_secret": encrypted}}
        result = decrypt_auth_fields(config, encryption_key=None)
        assert result["oauth"]["client_secret"] == encrypted

    def test_empty_config(self):
        assert decrypt_auth_fields({}, encryption_key=_KEY) == {}
        assert decrypt_auth_fields(None, encryption_key=_KEY) is None


class TestAuthFieldsRoundTrip:
    def test_oauth_client_secret_roundtrip(self):
        original = {"oauth": {"client_id": "cid", "client_secret": "super_secret_value"}}
        encrypted = encrypt_auth_fields(original, encryption_key=_KEY)
        assert is_encrypted(encrypted["oauth"]["client_secret"])
        decrypted = decrypt_auth_fields(encrypted, encryption_key=_KEY)
        assert decrypted["oauth"]["client_secret"] == "super_secret_value"
        assert decrypted["oauth"]["client_id"] == "cid"

    def test_apikey_roundtrip(self):
        original = {"apiKey": {"key": "my_api_key_value", "authorization_type": "bearer"}}
        encrypted = encrypt_auth_fields(original, encryption_key=_KEY)
        assert encrypted["apiKey"]["key"] != "my_api_key_value"
        decrypted = decrypt_auth_fields(encrypted, encryption_key=_KEY)
        assert decrypted["apiKey"]["key"] == "my_api_key_value"

    def test_double_encrypt_does_not_reencrypt(self):
        original = {"oauth": {"client_secret": "secret"}}
        once = encrypt_auth_fields(original, encryption_key=_KEY)
        twice = encrypt_auth_fields(once, encryption_key=_KEY)
        assert once["oauth"]["client_secret"] == twice["oauth"]["client_secret"]
