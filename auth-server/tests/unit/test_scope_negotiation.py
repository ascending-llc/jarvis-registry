"""
Unit tests for scope negotiation logic in OAuth authorization code flow.
"""

import pytest


@pytest.mark.unit
class TestScopeNegotiationLogic:
    """Unit tests for scope intersection logic."""

    def test_scope_intersection_subset(self):
        """Test intersection when requested scopes are subset of user scopes."""
        user_scopes = ["servers-read", "agents-read", "servers-write", "agents-write"]
        requested_scopes = ["servers-read", "agents-read"]

        resolved = [s for s in requested_scopes if s in user_scopes]

        assert resolved == ["servers-read", "agents-read"]
        assert len(resolved) == 2

    def test_scope_intersection_superset(self):
        """Test intersection when requested scopes include unavailable scopes."""
        user_scopes = ["servers-read", "agents-read"]
        requested_scopes = ["servers-read", "agents-read", "servers-write", "system-ops"]

        resolved = [s for s in requested_scopes if s in user_scopes]

        # Only the intersection
        assert resolved == ["servers-read", "agents-read"]
        assert "servers-write" not in resolved
        assert "system-ops" not in resolved

    def test_scope_intersection_empty(self):
        """Test intersection when no common scopes exist."""
        user_scopes = ["servers-read", "agents-read"]
        requested_scopes = ["system-ops", "admin-access"]

        resolved = [s for s in requested_scopes if s in user_scopes]

        assert len(resolved) == 0

    def test_scope_intersection_exact_match(self):
        """Test intersection when requested scopes exactly match user scopes."""
        user_scopes = ["servers-read", "agents-read"]
        requested_scopes = ["servers-read", "agents-read"]

        resolved = [s for s in requested_scopes if s in user_scopes]

        assert resolved == requested_scopes

    def test_scope_intersection_single_scope(self):
        """Test intersection with single scope."""
        user_scopes = ["servers-read", "agents-read", "servers-write"]
        requested_scopes = ["servers-read"]

        resolved = [s for s in requested_scopes if s in user_scopes]

        assert resolved == ["servers-read"]

    def test_scope_intersection_order_preserved(self):
        """Test that intersection preserves order of requested scopes."""
        user_scopes = ["agents-read", "servers-read", "servers-write"]
        requested_scopes = ["servers-read", "agents-read", "servers-write"]

        resolved = [s for s in requested_scopes if s in user_scopes]

        # Should preserve order from requested_scopes
        assert resolved == ["servers-read", "agents-read", "servers-write"]

    def test_scope_parsing_from_string(self):
        """Test parsing scope string into list."""
        scope_string = "servers-read agents-read servers-write"
        scopes_list = scope_string.split()

        assert scopes_list == ["servers-read", "agents-read", "servers-write"]

    def test_scope_joining_to_string(self):
        """Test joining scope list into string."""
        scopes_list = ["servers-read", "agents-read"]
        scope_string = " ".join(scopes_list)

        assert scope_string == "servers-read agents-read"

    def test_scope_empty_string_handling(self):
        """Test handling of empty scope string."""
        scope_string = ""
        scopes_list = scope_string.split() if scope_string else []

        assert scopes_list == []

    def test_scope_none_handling(self):
        """Test handling of None scope."""
        scope_string = None

        # Simulate the logic in callback
        if scope_string:
            requested_scopes = scope_string.split()
            use_default = False
        else:
            requested_scopes = []
            use_default = True

        assert use_default is True
        assert requested_scopes == []


@pytest.mark.unit
class TestRefreshTokenRotationLogic:
    """Unit tests for refresh token rotation logic."""

    def test_new_refresh_token_is_different(self):
        """Test that new refresh token is different from old one."""
        import secrets

        old_token = secrets.token_urlsafe(32)
        new_token = secrets.token_urlsafe(32)

        assert old_token != new_token

    def test_refresh_token_expiry_calculation(self):
        """Test refresh token expiry calculation (14 days)."""
        import time

        current_time = int(time.time())
        fourteen_days_seconds = 1209600
        expires_at = current_time + fourteen_days_seconds

        # Verify expires_at is approximately 14 days from now
        assert expires_at > current_time + 1209500  # At least 14 days minus 100s
        assert expires_at <= current_time + 1209600  # At most 14 days

    def test_refresh_token_data_structure(self):
        """Test refresh token data structure in storage."""
        import time

        refresh_token_data = {
            "client_id": "test-client",
            "user_info": {"username": "test_user", "email": "test@example.com", "groups": []},
            "scope": "servers-read agents-read",
            "expires_at": int(time.time()) + 1209600,
        }

        # Verify all required fields present
        assert "client_id" in refresh_token_data
        assert "user_info" in refresh_token_data
        assert "scope" in refresh_token_data
        assert "expires_at" in refresh_token_data

        # Verify user_info structure
        assert "username" in refresh_token_data["user_info"]
        assert "email" in refresh_token_data["user_info"]
        assert "groups" in refresh_token_data["user_info"]

    def test_refresh_token_storage_cleanup(self):
        """Test that old token is removed from storage."""
        storage = {"old_token": {"data": "value"}}

        # Simulate rotation
        old_token = "old_token"
        new_token = "new_token"

        # Remove old token
        if old_token in storage:
            del storage[old_token]

        # Add new token
        storage[new_token] = {"data": "value"}

        assert old_token not in storage
        assert new_token in storage
