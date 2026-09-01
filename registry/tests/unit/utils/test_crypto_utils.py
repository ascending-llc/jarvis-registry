"""Unit tests for crypto_utils module."""

import time
from unittest.mock import patch

from registry.core.config import settings
from registry.utils.crypto_utils import (
    decrypt_auth_fields,
    decrypt_value,
    encrypt_auth_fields,
    encrypt_value,
    generate_access_token,
    generate_refresh_token,
    is_encrypted,
    verify_access_token,
    verify_refresh_token,
)
from registry_pkgs.core.crypto_utils import ENCRYPTED_VALUE_PATTERN
from registry_pkgs.core.jwt_tokens import (
    TOKEN_CLASS_CLAIM,
    TOKEN_CLASS_CRUD_SESSION,
    mint_managed_agent_token,
)


class TestWrapperDelegation:
    """Verify registry wrappers pass the configured encryption key."""

    def test_encrypt_value_passes_settings_key(self):
        with patch("registry.utils.crypto_utils._encrypt_value") as mock_encrypt:
            mock_encrypt.return_value = "ct"
            assert encrypt_value("secret") == "ct"
            mock_encrypt.assert_called_once_with("secret", encryption_key=settings.encryption_key)

    def test_decrypt_value_passes_settings_key(self):
        with patch("registry.utils.crypto_utils._decrypt_value") as mock_decrypt:
            mock_decrypt.return_value = "pt"
            assert decrypt_value("ct") == "pt"
            mock_decrypt.assert_called_once_with("ct", encryption_key=settings.encryption_key)

    def test_round_trip_through_wrappers(self):
        ciphertext = encrypt_value("round-trip-secret")
        assert is_encrypted(ciphertext)
        assert decrypt_value(ciphertext) == "round-trip-secret"


class TestCrudSessionTokens:
    """Cookie (CRUD-session) token round-trips and cross-class rejection (AS-1523)."""

    @staticmethod
    def _gen_access() -> str:
        return generate_access_token(
            user_id="u1",
            username="alice",
            email="alice@example.com",
            groups=["g1"],
            scopes=["servers-read"],
            role="user",
            auth_method="oauth2",
            provider="entra",
        )

    def test_access_token_roundtrip_and_class(self):
        token = self._gen_access()
        claims = verify_access_token(token)
        assert claims is not None
        assert claims["sub"] == "alice"
        assert claims["aud"] == settings.jwt_audience_crud_services
        assert claims["client_id"] == settings.registry_app_name
        assert claims[TOKEN_CLASS_CLAIM] == TOKEN_CLASS_CRUD_SESSION
        assert claims["token_type"] == "access_token"

    def test_refresh_token_roundtrip(self):
        token = generate_refresh_token(
            user_id="u1",
            username="alice",
            auth_method="oauth2",
            provider="entra",
            groups=["g1"],
            scopes=["servers-read"],
            role="user",
            email="alice@example.com",
        )
        claims = verify_refresh_token(token)
        assert claims is not None
        assert claims["token_type"] == "refresh_token"
        assert claims["client_id"] == settings.registry_app_name

    def test_refresh_token_session_started_at_defaults_to_now(self):
        before = int(time.time())
        token = generate_refresh_token(
            user_id="u1",
            username="alice",
            auth_method="oauth2",
            provider="entra",
            groups=["g1"],
            scopes=["servers-read"],
            role="user",
            email="alice@example.com",
        )
        after = int(time.time())
        claims = verify_refresh_token(token)
        assert claims is not None
        assert "session_started_at" in claims
        assert before <= claims["session_started_at"] <= after

    def test_refresh_token_session_started_at_carried_forward(self):
        fixed_ts = 1_700_000_000
        token = generate_refresh_token(
            user_id="u1",
            username="alice",
            auth_method="oauth2",
            provider="entra",
            groups=["g1"],
            scopes=["servers-read"],
            role="user",
            email="alice@example.com",
            session_started_at=fixed_ts,
        )
        claims = verify_refresh_token(token)
        assert claims is not None
        assert claims["session_started_at"] == fixed_ts

    def test_access_verifier_rejects_refresh_token(self):
        token = generate_refresh_token(
            user_id="u1",
            username="alice",
            auth_method="oauth2",
            provider="entra",
            groups=[],
            scopes=["servers-read"],
            role="user",
            email="alice@example.com",
        )
        assert verify_access_token(token) is None

    def test_access_verifier_rejects_managed_agent_token(self):
        # A leaked managed-agent (proxy) token must never validate as a CRUD session cookie.
        managed = mint_managed_agent_token(
            settings.jwt_token_config,
            subject="alice",
            client_id="mcp-client-abc",
            requested_scopes=["mcp-proxy-ops"],
            expires_in_seconds=3600,
        )
        assert verify_access_token(managed) is None


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


class TestAuthFieldsWrapperDelegation:
    """The registry wrappers delegate to registry_pkgs with the configured key.

    The auth-field crypto logic itself is tested in
    registry-pkgs/tests/unit/core/test_crypto_utils.py.
    """

    def test_encrypt_auth_fields_passes_settings_key(self):
        with patch("registry.utils.crypto_utils._encrypt_auth_fields") as mock_encrypt:
            mock_encrypt.return_value = {"ok": True}
            config = {"oauth": {"client_secret": "s"}}
            assert encrypt_auth_fields(config) == {"ok": True}
            mock_encrypt.assert_called_once_with(config, encryption_key=settings.encryption_key)

    def test_decrypt_auth_fields_passes_settings_key(self):
        with patch("registry.utils.crypto_utils._decrypt_auth_fields") as mock_decrypt:
            mock_decrypt.return_value = {"ok": True}
            config = {"oauth": {"client_secret": "s"}}
            assert decrypt_auth_fields(config) == {"ok": True}
            mock_decrypt.assert_called_once_with(config, encryption_key=settings.encryption_key)

    def test_no_creds_key_passes_none(self):
        with (
            patch("registry.utils.crypto_utils.settings") as mock_settings,
            patch("registry.utils.crypto_utils._decrypt_auth_fields") as mock_decrypt,
        ):
            mock_settings.creds_key = ""
            mock_decrypt.return_value = {}
            decrypt_auth_fields({"oauth": {}})
            mock_decrypt.assert_called_once_with({"oauth": {}}, encryption_key=None)

    def test_wrapper_round_trip(self):
        """End-to-end through the registry wrappers with the real key."""
        config = {"oauth": {"client_id": "cid", "client_secret": "super_secret_value"}}
        encrypted = encrypt_auth_fields(config)
        assert is_encrypted(encrypted["oauth"]["client_secret"])
        decrypted = decrypt_auth_fields(encrypted)
        assert decrypted["oauth"]["client_secret"] == "super_secret_value"
