"""Unit tests for OAuth utility functions."""

import pytest

from registry_pkgs.oauth.oauth_utils import get_default_redirect_uri, parse_scope, scope_to_string


class TestGetDefaultRedirectUri:
    """Tests for get_default_redirect_uri utility function (base_url now caller-supplied)"""

    def test_get_default_redirect_uri_with_leading_slash(self):
        result = get_default_redirect_uri("/notion", base_url="http://localhost:7860")
        assert result == "http://localhost:7860/api/v1/mcp/notion/oauth/callback"

    def test_get_default_redirect_uri_without_leading_slash(self):
        result = get_default_redirect_uri("brave", base_url="http://localhost:7860")
        assert result == "http://localhost:7860/api/v1/mcp/brave/oauth/callback"

    def test_get_default_redirect_uri_with_trailing_slash(self):
        result = get_default_redirect_uri("/github/", base_url="http://localhost:7860")
        assert result == "http://localhost:7860/api/v1/mcp/github/oauth/callback"

    def test_get_default_redirect_uri_production_url(self):
        result = get_default_redirect_uri("/google-drive", base_url="https://registry.example.com")
        assert result == "https://registry.example.com/api/v1/mcp/google-drive/oauth/callback"

    def test_get_default_redirect_uri_with_port(self):
        result = get_default_redirect_uri("slack", base_url="http://localhost:3000")
        assert result == "http://localhost:3000/api/v1/mcp/slack/oauth/callback"


class TestParseScope:
    """Tests for parse_scope utility function"""

    def test_parse_scope_from_string_space_separated(self):
        """Test parsing space-separated scope string"""
        result = parse_scope("read write delete")
        assert result == ["read", "write", "delete"]

    def test_parse_scope_from_string_comma_separated(self):
        """Test parsing comma-separated scope string"""
        result = parse_scope("read,write,delete")
        assert result == ["read", "write", "delete"]

    def test_parse_scope_from_list(self):
        """Test parsing scope from list"""
        result = parse_scope(["read", "write", "delete"])
        assert result == ["read", "write", "delete"]

    def test_parse_scope_none_with_default(self):
        """Test parsing None scope with default value"""
        result = parse_scope(None, default=["default_scope"])
        assert result == ["default_scope"]

    def test_parse_scope_none_without_default(self):
        """Test parsing None scope without default value"""
        result = parse_scope(None)
        assert result == []

    def test_parse_scope_empty_string(self):
        """Test parsing empty string scope"""
        result = parse_scope("")
        assert result == []

    def test_parse_scope_with_extra_spaces(self):
        """Test parsing scope with extra spaces"""
        result = parse_scope("  read   write  ")
        assert result == ["read", "write"]


class TestScopeToString:
    """Tests for scope_to_string utility function"""

    def test_scope_to_string_from_list(self):
        """Test converting list to space-separated string"""
        result = scope_to_string(["read", "write", "delete"])
        assert result == "read write delete"

    def test_scope_to_string_from_string(self):
        """Test converting string to string (passthrough)"""
        result = scope_to_string("read write delete")
        assert result == "read write delete"

    def test_scope_to_string_none(self):
        """Test converting None to empty string"""
        result = scope_to_string(None)
        assert result == ""

    def test_scope_to_string_empty_list(self):
        """Test converting empty list to empty string"""
        result = scope_to_string([])
        assert result == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
