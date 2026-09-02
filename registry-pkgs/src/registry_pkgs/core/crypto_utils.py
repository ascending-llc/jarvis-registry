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


def encrypt_auth_fields(config: dict, *, encryption_key: bytes | None) -> dict:
    """
    Encrypt sensitive authentication fields in server config.

    Handles two authentication patterns:
    1. oauth.client_secret - OAuth client secret
    2. apiKey.key - API key value

    Args:
        config: Server configuration dictionary
        encryption_key: AES key bytes (caller-supplied; not read from any settings).
            Pass ``None`` when no key is configured — fields are then left as plaintext
            and a warning is logged.

    Returns:
        dict: Config with encrypted sensitive fields

    Note:
        If ``encryption_key`` is None, values will be stored as plaintext.
        A warning will be logged in this case.
    """
    if not config:
        return config

    config = config.copy()

    if encryption_key is None:
        logger.warning(
            "CREDS_KEY configuration is not set. "
            "Sensitive authentication fields will be stored as PLAINTEXT. "
            "Set CREDS_KEY environment variable to enable encryption of credentials."
        )
        return config

    try:
        # Handle oauth field
        if "oauth" in config and isinstance(config["oauth"], dict):
            oauth = config["oauth"].copy()

            if "client_secret" in oauth:
                client_secret = oauth["client_secret"]
                if client_secret and not is_encrypted(str(client_secret)):
                    # Only encrypt if not already encrypted
                    try:
                        oauth["client_secret"] = encrypt_value(str(client_secret), encryption_key=encryption_key)
                        config["oauth"] = oauth
                        logger.debug("Encrypted oauth.client_secret")
                    except Exception as encrypt_error:
                        logger.error(f"Failed to encrypt oauth.client_secret: {encrypt_error}")
                        # Keep plaintext value

        # Handle apiKey field
        if "apiKey" in config and isinstance(config["apiKey"], dict):
            api_key = config["apiKey"].copy()

            if "key" in api_key:
                key_value = api_key["key"]
                if key_value and ":" not in str(key_value):
                    # Only encrypt if not already encrypted
                    try:
                        api_key["key"] = encrypt_value(str(key_value), encryption_key=encryption_key)
                        config["apiKey"] = api_key
                        logger.debug("Encrypted apiKey.key")
                    except Exception as encrypt_error:
                        logger.error(f"Failed to encrypt apiKey.key: {encrypt_error}")
                        # Keep plaintext value

    except Exception as e:
        logger.error(f"Failed to encrypt auth fields: {e}", exc_info=True)
        # Return original config if encryption fails
        return config

    return config


def decrypt_auth_fields(config: dict, *, encryption_key: bytes | None) -> dict:
    """
    Decrypt sensitive authentication fields in server config.

    Handles two authentication patterns:
    1. oauth.client_secret - OAuth client secret
    2. apiKey.key - API key value

    Args:
        config: Server configuration dictionary with encrypted fields
        encryption_key: AES key bytes (caller-supplied; not read from any settings).
            Pass ``None`` when no key is configured — encrypted values are then returned
            as-is (still encrypted) and a warning is logged.

    Returns:
        dict: Config with decrypted sensitive fields

    Note:
        If ``encryption_key`` is None, encrypted values will be returned as-is (still
        encrypted). This prevents the API from crashing when no key is configured.
    """
    if not config:
        return config

    config = config.copy()

    if encryption_key is None:
        logger.warning(
            "CREDS_KEY configuration is not set. "
            "Encrypted authentication fields will be returned as-is (still encrypted). "
            "Set CREDS_KEY environment variable to decrypt sensitive credentials."
        )
        return config

    try:
        # Handle oauth field
        if "oauth" in config and isinstance(config["oauth"], dict):
            oauth = config["oauth"].copy()

            if "client_secret" in oauth:
                client_secret = oauth["client_secret"]
                if client_secret:
                    try:
                        oauth["client_secret"] = decrypt_value(str(client_secret), encryption_key=encryption_key)
                        config["oauth"] = oauth
                        logger.debug("Decrypted oauth.client_secret")
                    except Exception as decrypt_error:
                        logger.warning(f"Failed to decrypt oauth.client_secret: {decrypt_error}")
                        # Keep encrypted value

        # Handle apiKey field
        if "apiKey" in config and isinstance(config["apiKey"], dict):
            api_key = config["apiKey"].copy()

            if "key" in api_key:
                key_value = api_key["key"]
                if key_value:
                    try:
                        api_key["key"] = decrypt_value(str(key_value), encryption_key=encryption_key)
                        config["apiKey"] = api_key
                        logger.debug("Decrypted apiKey.key")
                    except Exception as decrypt_error:
                        logger.warning(f"Failed to decrypt apiKey.key: {decrypt_error}")
                        # Keep encrypted value

    except Exception as e:
        logger.error(f"Failed to decrypt auth fields: {e}", exc_info=True)
        # Return original config if decryption fails
        return config

    return config
