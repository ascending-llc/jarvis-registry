"""Unit tests for crypto_utils module."""

from unittest.mock import patch

from registry.utils.crypto_utils import (
    ENCRYPTED_VALUE_PATTERN,
    decrypt_auth_fields,
    encrypt_auth_fields,
    is_encrypted,
)


class TestIsEncrypted:
    """Tests for is_encrypted() function"""

    def test_valid_encrypted_format(self):
        """Test that valid encrypted format is detected"""
        # 32 hex chars (16 bytes IV) followed by colon and ciphertext
        encrypted = "a1b2c3d4e5f67890abcdef1234567890:1234567890abcdef"
        assert is_encrypted(encrypted) is True

    def test_invalid_format_no_colon(self):
        """Test that string without colon is not detected as encrypted"""
        plaintext = "my_secret_value"
        assert is_encrypted(plaintext) is False

    def test_invalid_format_short_iv(self):
        """Test that string with short IV is not detected as encrypted"""
        # Only 16 hex chars instead of 32
        invalid = "a1b2c3d4e5f67890:ciphertext"
        assert is_encrypted(invalid) is False

    def test_invalid_format_non_hex(self):
        """Test that string with non-hex chars is not detected as encrypted"""
        invalid = "GHIJKLMNOPQRSTUVWXYZ123456789012:ciphertext"
        assert is_encrypted(invalid) is False

    def test_empty_string(self):
        """Test that empty string is not encrypted"""
        assert is_encrypted("") is False

    def test_none_value(self):
        """Test that None is not encrypted"""
        assert is_encrypted(None) is False

    def test_colon_in_middle_not_encrypted(self):
        """Test that random string with colon in middle is not encrypted"""
        # Colon exists but not at position 32
        plaintext = "user:password"
        assert is_encrypted(plaintext) is False


class TestEncryptionPattern:
    """Tests for ENCRYPTED_VALUE_PATTERN regex"""

    def test_pattern_matches_valid_format(self):
        """Test regex pattern matches valid encrypted format"""
        valid = "0123456789abcdef0123456789abcdef:data"
        assert ENCRYPTED_VALUE_PATTERN.match(valid) is not None

    def test_pattern_rejects_uppercase_hex(self):
        """Test regex pattern rejects uppercase hex chars"""
        invalid = "0123456789ABCDEF0123456789ABCDEF:data"
        assert ENCRYPTED_VALUE_PATTERN.match(invalid) is None

    def test_pattern_rejects_short_iv(self):
        """Test regex pattern rejects IV shorter than 32 chars"""
        invalid = "0123456789abcdef:data"
        assert ENCRYPTED_VALUE_PATTERN.match(invalid) is None


class TestEncryptAuthFields:
    """Tests for encrypt_auth_fields() function"""

    @patch("registry.utils.crypto_utils.settings")
    @patch("registry.utils.crypto_utils.encrypt_value")
    def test_encrypt_oauth_client_secret(self, mock_encrypt, mock_settings):
        """Test that oauth.client_secret is encrypted"""
        mock_settings.creds_key = b"test_key_32_bytes_long_exactly!"
        mock_encrypt.return_value = "a" * 32 + ":encrypted_secret"

        config = {
            "oauth": {
                "client_id": "test_client",
                "client_secret": "my_secret",
                "authorization_url": "https://example.com/oauth/authorize",
            }
        }

        result = encrypt_auth_fields(config)

        mock_encrypt.assert_called_once_with("my_secret")
        assert result["oauth"]["client_secret"] == "a" * 32 + ":encrypted_secret"
        assert result["oauth"]["client_id"] == "test_client"

    @patch("registry.utils.crypto_utils.settings")
    def test_encrypt_oauth_already_encrypted(self, mock_settings):
        """Test that already encrypted oauth.client_secret is not re-encrypted"""
        mock_settings.creds_key = b"test_key_32_bytes_long_exactly!"

        already_encrypted = "0123456789abcdef0123456789abcdef:encrypted_data"
        config = {
            "oauth": {
                "client_id": "test_client",
                "client_secret": already_encrypted,
            }
        }

        result = encrypt_auth_fields(config)

        # Should remain unchanged
        assert result["oauth"]["client_secret"] == already_encrypted

    @patch("registry.utils.crypto_utils.settings")
    @patch("registry.utils.crypto_utils.encrypt_value")
    def test_encrypt_apikey_still_works(self, mock_encrypt, mock_settings):
        """Test that apiKey.key encryption still works (no regression)"""
        mock_settings.creds_key = b"test_key_32_bytes_long_exactly!"
        mock_encrypt.return_value = "b" * 32 + ":encrypted_key"

        config = {
            "apiKey": {
                "key": "my_api_key",
                "authorization_type": "bearer",
            }
        }

        result = encrypt_auth_fields(config)

        mock_encrypt.assert_called_once_with("my_api_key")
        assert result["apiKey"]["key"] == "b" * 32 + ":encrypted_key"

    @patch("registry.utils.crypto_utils.settings")
    def test_no_creds_key_returns_unchanged(self, mock_settings):
        """Test that config is returned unchanged when CREDS_KEY is not set"""
        mock_settings.creds_key = None

        config = {
            "oauth": {
                "client_secret": "plaintext_secret",
            }
        }

        result = encrypt_auth_fields(config)

        # Should remain unchanged
        assert result["oauth"]["client_secret"] == "plaintext_secret"

    @patch("registry.utils.crypto_utils.settings")
    @patch("registry.utils.crypto_utils.encrypt_value")
    def test_encrypt_both_oauth_and_apikey(self, mock_encrypt, mock_settings):
        """Test that both oauth and apiKey can be encrypted in same config"""
        mock_settings.creds_key = b"test_key_32_bytes_long_exactly!"
        mock_encrypt.side_effect = [
            "a" * 32 + ":encrypted_oauth",
            "b" * 32 + ":encrypted_apikey",
        ]

        config = {
            "oauth": {"client_secret": "oauth_secret"},
            "apiKey": {"key": "api_key"},
        }

        result = encrypt_auth_fields(config)

        assert mock_encrypt.call_count == 2
        assert result["oauth"]["client_secret"] == "a" * 32 + ":encrypted_oauth"
        assert result["apiKey"]["key"] == "b" * 32 + ":encrypted_apikey"

    @patch("registry.utils.crypto_utils.settings")
    def test_empty_config(self, mock_settings):
        """Test that empty config is handled"""
        mock_settings.creds_key = b"test_key_32_bytes_long_exactly!"

        result = encrypt_auth_fields({})
        assert result == {}

        result = encrypt_auth_fields(None)
        assert result is None

    @patch("registry.utils.crypto_utils.settings")
    def test_oauth_without_client_secret(self, mock_settings):
        """Test that oauth config without client_secret is handled"""
        mock_settings.creds_key = b"test_key_32_bytes_long_exactly!"

        config = {
            "oauth": {
                "client_id": "test_client",
                "authorization_url": "https://example.com/oauth",
            }
        }

        result = encrypt_auth_fields(config)

        # Should remain unchanged, no client_secret to encrypt
        assert result["oauth"]["client_id"] == "test_client"
        assert "client_secret" not in result["oauth"]


class TestDecryptAuthFields:
    """Tests for decrypt_auth_fields() function"""

    @patch("registry.utils.crypto_utils.settings")
    @patch("registry.utils.crypto_utils.decrypt_value")
    def test_decrypt_oauth_client_secret(self, mock_decrypt, mock_settings):
        """Test that oauth.client_secret is decrypted"""
        mock_settings.creds_key = b"test_key_32_bytes_long_exactly!"
        mock_decrypt.return_value = "decrypted_secret"

        config = {
            "oauth": {
                "client_id": "test_client",
                "client_secret": "a" * 32 + ":encrypted_data",
            }
        }

        result = decrypt_auth_fields(config)

        mock_decrypt.assert_called_once_with("a" * 32 + ":encrypted_data")
        assert result["oauth"]["client_secret"] == "decrypted_secret"
        assert result["oauth"]["client_id"] == "test_client"

    @patch("registry.utils.crypto_utils.settings")
    @patch("registry.utils.crypto_utils.decrypt_value")
    def test_decrypt_apikey_still_works(self, mock_decrypt, mock_settings):
        """Test that apiKey.key decryption still works (no regression)"""
        mock_settings.creds_key = b"test_key_32_bytes_long_exactly!"
        mock_decrypt.return_value = "decrypted_key"

        config = {
            "apiKey": {
                "key": "b" * 32 + ":encrypted_data",
                "authorization_type": "bearer",
            }
        }

        result = decrypt_auth_fields(config)

        mock_decrypt.assert_called_once_with("b" * 32 + ":encrypted_data")
        assert result["apiKey"]["key"] == "decrypted_key"

    @patch("registry.utils.crypto_utils.settings")
    def test_no_creds_key_returns_unchanged(self, mock_settings):
        """Test that config is returned unchanged when CREDS_KEY is not set"""
        mock_settings.creds_key = None

        config = {
            "oauth": {
                "client_secret": "a" * 32 + ":encrypted",
            }
        }

        result = decrypt_auth_fields(config)

        # Should remain unchanged (still encrypted)
        assert result["oauth"]["client_secret"] == "a" * 32 + ":encrypted"

    @patch("registry.utils.crypto_utils.settings")
    @patch("registry.utils.crypto_utils.decrypt_value")
    def test_decrypt_both_oauth_and_apikey(self, mock_decrypt, mock_settings):
        """Test that both oauth and apiKey can be decrypted in same config"""
        mock_settings.creds_key = b"test_key_32_bytes_long_exactly!"
        mock_decrypt.side_effect = ["decrypted_oauth", "decrypted_apikey"]

        config = {
            "oauth": {"client_secret": "a" * 32 + ":encrypted_oauth"},
            "apiKey": {"key": "b" * 32 + ":encrypted_apikey"},
        }

        result = decrypt_auth_fields(config)

        assert mock_decrypt.call_count == 2
        assert result["oauth"]["client_secret"] == "decrypted_oauth"
        assert result["apiKey"]["key"] == "decrypted_apikey"

    @patch("registry.utils.crypto_utils.settings")
    def test_empty_config(self, mock_settings):
        """Test that empty config is handled"""
        mock_settings.creds_key = b"test_key_32_bytes_long_exactly!"

        result = decrypt_auth_fields({})
        assert result == {}

        result = decrypt_auth_fields(None)
        assert result is None


class TestEncryptDecryptRoundTrip:
    """Integration tests for encrypt/decrypt round trip"""

    @patch("registry.utils.crypto_utils.settings")
    def test_oauth_client_secret_roundtrip(self, mock_settings):
        """Test that oauth.client_secret can be encrypted and decrypted"""
        # AES requires key to be exactly 16, 24, or 32 bytes
        test_key = b"12345678901234567890123456789012"  # 32 bytes
        mock_settings.creds_key = test_key
        mock_settings.encryption_key = test_key

        original_config = {
            "oauth": {
                "client_id": "test_client",
                "client_secret": "super_secret_value",
                "authorization_url": "https://example.com/oauth",
            }
        }

        # Encrypt
        encrypted_config = encrypt_auth_fields(original_config)
        assert encrypted_config["oauth"]["client_secret"] != "super_secret_value"
        assert is_encrypted(encrypted_config["oauth"]["client_secret"])

        # Decrypt
        decrypted_config = decrypt_auth_fields(encrypted_config)
        assert decrypted_config["oauth"]["client_secret"] == "super_secret_value"
        assert decrypted_config["oauth"]["client_id"] == "test_client"

    @patch("registry.utils.crypto_utils.settings")
    def test_apikey_roundtrip(self, mock_settings):
        """Test that apiKey.key can be encrypted and decrypted"""
        # AES requires key to be exactly 16, 24, or 32 bytes
        test_key = b"12345678901234567890123456789012"  # 32 bytes
        mock_settings.creds_key = test_key
        mock_settings.encryption_key = test_key

        original_config = {
            "apiKey": {
                "key": "my_api_key_value",
                "authorization_type": "bearer",
            }
        }

        # Encrypt
        encrypted_config = encrypt_auth_fields(original_config)
        assert encrypted_config["apiKey"]["key"] != "my_api_key_value"
        assert ":" in encrypted_config["apiKey"]["key"]

        # Decrypt
        decrypted_config = decrypt_auth_fields(encrypted_config)
        assert decrypted_config["apiKey"]["key"] == "my_api_key_value"

    @patch("registry.utils.crypto_utils.settings")
    def test_double_encrypt_does_not_reencrypt(self, mock_settings):
        """Test that encrypting twice doesn't double-encrypt"""
        # AES requires key to be exactly 16, 24, or 32 bytes
        test_key = b"12345678901234567890123456789012"  # 32 bytes
        mock_settings.creds_key = test_key
        mock_settings.encryption_key = test_key

        original_config = {
            "oauth": {
                "client_secret": "my_secret",
            }
        }

        # Encrypt once
        encrypted_once = encrypt_auth_fields(original_config)
        first_encrypted = encrypted_once["oauth"]["client_secret"]

        # Encrypt again
        encrypted_twice = encrypt_auth_fields(encrypted_once)
        second_encrypted = encrypted_twice["oauth"]["client_secret"]

        # Should be the same (not double-encrypted)
        assert first_encrypted == second_encrypted

        # And should still decrypt correctly
        decrypted = decrypt_auth_fields(encrypted_twice)
        assert decrypted["oauth"]["client_secret"] == "my_secret"
