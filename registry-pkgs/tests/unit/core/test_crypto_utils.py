"""Unit tests for shared cryptographic utilities."""

import pytest

from registry_pkgs.core.crypto_utils import (
    ENCRYPTED_VALUE_PATTERN,
    decrypt_value,
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
