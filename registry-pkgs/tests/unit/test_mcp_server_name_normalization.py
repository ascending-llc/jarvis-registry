"""
Unit tests for ExtendedMCPServer serverName normalization (AS-1855).

normalize_server_name() must stay byte-for-byte identical to librechat-data-provider's
normalizeServerName() (packages/data-provider/src/config.ts:3532-3549), since Chat's Mongoose
schema and Registry's Beanie model share the same `mcpservers` collection and unique index.
"""

import pytest
from beanie import PydanticObjectId

from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer, normalize_server_name


class TestNormalizeServerName:
    """Test that normalize_server_name mirrors the TS implementation's three code paths."""

    @pytest.mark.parametrize(
        "server_name",
        [
            "github",
            "github-server",
            "github_server",
            "github.server",
            "GitHubServer123",
            "a",
        ],
    )
    def test_charset_safe_name_passes_through_unchanged(self, server_name):
        """A serverName already limited to [a-zA-Z0-9_.-] is returned as-is."""
        assert normalize_server_name(server_name) == server_name

    @pytest.mark.parametrize(
        ("server_name", "expected"),
        [
            ("My Server!!", "My_Server"),
            ("github server", "github_server"),
            ("  spaced out  ", "spaced_out"),
            ("weird/server/name", "weird_server_name"),
            ("café", "caf"),
        ],
    )
    def test_unsafe_chars_are_substituted_and_trimmed(self, server_name, expected):
        """Unsafe characters become underscores; leading/trailing underscores are stripped."""
        assert normalize_server_name(server_name) == expected

    @pytest.mark.parametrize(
        ("server_name", "expected_hash_suffix"),
        [
            ("!!!", "32769"),
            ("   ", "31776"),
        ],
    )
    def test_all_invalid_chars_fall_back_to_deterministic_hash(self, server_name, expected_hash_suffix):
        """When every character is unsafe, the result hashes the original string."""
        result = normalize_server_name(server_name)
        assert result == f"server_{expected_hash_suffix}"

    def test_hash_fallback_handles_astral_characters_like_js_utf16_code_units(self):
        """An emoji (astral character) must hash over UTF-16 code units, matching JS's charCodeAt."""
        # U+1F600 (😀) is a UTF-16 surrogate pair: high 0xD83D (55357), low 0xDE00 (56832).
        # hash = ((0*31 + 55357) * 31) + 56832 = 1772899, matching the TS implementation exactly.
        assert normalize_server_name("😀") == "server_1772899"

    def test_hash_fallback_is_deterministic(self):
        """The same all-invalid input always normalizes to the same hashed name."""
        assert normalize_server_name("###") == normalize_server_name("###")

    def test_hash_fallback_never_collapses_to_empty_after_prefix(self):
        """The hash fallback always returns a non-empty, charset-safe result."""
        result = normalize_server_name("!!!")
        assert result
        assert result.startswith("server_")


class TestExtendedMCPServerNormalizedServerNameField:
    """Test that normalizedServerName is Optional and decoupled from Pydantic validation."""

    def test_construction_without_normalized_server_name_does_not_raise(self, monkeypatch):
        """A dict with no normalizedServerName key at all must not raise ValidationError."""
        monkeypatch.setattr(ExtendedMCPServer, "get_pymongo_collection", classmethod(lambda cls: None))

        server = ExtendedMCPServer.model_validate(
            {
                "serverName": "legacy-server",
                "config": {},
                "author": str(PydanticObjectId()),
            }
        )

        assert server.normalizedServerName is None

    def test_construction_with_normalized_server_name_preserves_value(self, monkeypatch):
        monkeypatch.setattr(ExtendedMCPServer, "get_pymongo_collection", classmethod(lambda cls: None))

        server = ExtendedMCPServer.model_validate(
            {
                "serverName": "github",
                "normalizedServerName": "github",
                "config": {},
                "author": str(PydanticObjectId()),
            }
        )

        assert server.normalizedServerName == "github"
