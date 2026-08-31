"""
Cryptographic utilities for encrypting/decrypting sensitive data.

This module provides AES-CBC encryption compatible with the TypeScript
encryption implementation used elsewhere in the system.

TypeScript equivalent:
- Algorithm: AES-CBC
- Key derivation: caller-supplied ``encryption_key`` bytes (registry passes
  ``settings.encryption_key``); this module is shared and must not read any app
  settings singleton.
- IV: Random 16 bytes per encryption
- Format: hex(iv):hex(ciphertext)
"""

import logging
import os
import re

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

logger = logging.getLogger(__name__)


# Algorithm constants
ALGORITHM = "AES-CBC"
IV_LENGTH = 16  # 128 bits

# Encryption format validation
# Encrypted values have format: hex(iv):hex(ciphertext)
# IV is always 16 bytes = 32 hex characters
ENCRYPTED_VALUE_PATTERN = re.compile(r"^[0-9a-f]{32}:")


def is_encrypted(value: str) -> bool:
    """
    Check if a value is already encrypted using strict pattern matching.

    Encrypted values have the format: hex(iv):hex(ciphertext)
    where IV is always 16 bytes (32 hex characters).

    Args:
        value: String value to check

    Returns:
        True if value matches encrypted format, False otherwise
    """
    if not value or not isinstance(value, str):
        return False
    return bool(ENCRYPTED_VALUE_PATTERN.match(value))


def encrypt_value(plaintext: str, *, encryption_key: bytes) -> str:
    """
    Encrypts a value using AES-CBC with a random IV.

    This implementation is compatible with the TypeScript encryptV2 function:
    - Uses AES-CBC encryption (matching Web Crypto API)
    - Generates a random 16-byte IV for each encryption
    - Returns format: hex(iv):hex(ciphertext)
    - Uses PKCS#7 padding to a 16-byte AES block size

    Args:
        plaintext: The plaintext string to encrypt
        encryption_key: AES key bytes (caller-supplied; not read from any settings)

    Returns:
        str: Encrypted string in format "iv_hex:ciphertext_hex"

    Raises:
        Exception: If encryption fails
    """
    if not plaintext:
        return plaintext

    try:
        # Generate random IV
        gen_iv = os.urandom(IV_LENGTH)

        # Encode plaintext
        plaintext_bytes = plaintext.encode("utf-8")

        # Pad to 16-byte boundary (AES block size)
        block_size = 16
        padding_length = block_size - (len(plaintext_bytes) % block_size)
        padded_data = plaintext_bytes + bytes([padding_length] * padding_length)

        # Create cipher
        cipher = Cipher(algorithms.AES(encryption_key), modes.CBC(gen_iv), backend=default_backend())
        encryptor = cipher.encryptor()

        # Encrypt
        ciphertext = encryptor.update(padded_data) + encryptor.finalize()

        # Return as hex(iv):hex(ciphertext)
        return gen_iv.hex() + ":" + ciphertext.hex()

    except Exception as e:
        logger.error(f"Encryption failed: {e}", exc_info=True)
        raise Exception(f"Failed to encrypt value: {e}")


def decrypt_value(encrypted_value: str, *, encryption_key: bytes) -> str:
    """
    Decrypts an encrypted value using AES-CBC.

    This implementation is compatible with the TypeScript decryptV2 function:
    - Expects format: hex(iv):hex(ciphertext)
    - Uses AES-CBC decryption (matching Web Crypto API)
    - Returns original plaintext

    If the value doesn't contain a colon separator, it's assumed to be
    already decrypted and returned as-is (for backward compatibility).

    Args:
        encrypted_value: The encrypted string in format "iv_hex:ciphertext_hex"
        encryption_key: AES key bytes (caller-supplied; not read from any settings)

    Returns:
        str: Decrypted plaintext string

    Raises:
        Exception: If decryption fails
    """
    if not encrypted_value:
        return encrypted_value

    # Check if value is encrypted (contains colon separator)
    parts = encrypted_value.split(":")
    if len(parts) == 1:
        # Not encrypted, return as-is (matching TS: if (parts.length === 1) return parts[0])
        return parts[0]

    try:
        # Split IV and ciphertext (matching TS logic)
        gen_iv = bytes.fromhex(parts[0])
        encrypted = ":".join(parts[1:])

        # Convert ciphertext from hex
        ciphertext = bytes.fromhex(encrypted)

        # Validate IV length
        if len(gen_iv) != IV_LENGTH:
            raise ValueError(f"Invalid IV length: expected {IV_LENGTH}, got {len(gen_iv)}")

        # Create cipher
        cipher = Cipher(algorithms.AES(encryption_key), modes.CBC(gen_iv), backend=default_backend())
        decryptor = cipher.decryptor()

        # Decrypt
        padded_plaintext = decryptor.update(ciphertext) + decryptor.finalize()

        # Remove padding (standard PKCS#7 unpadding)
        padding_length = padded_plaintext[-1]
        plaintext_bytes = padded_plaintext[:-padding_length]

        # Convert to string
        return plaintext_bytes.decode("utf-8")

    except Exception as e:
        logger.error(f"Decryption failed: {e}", exc_info=True)
        raise Exception(f"Failed to decrypt value: {e}")
