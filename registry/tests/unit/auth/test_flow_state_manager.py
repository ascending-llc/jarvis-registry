"""Unit tests for FlowStateManager — RFC 8707 resource indicator resolution (RC1)."""

import pytest

from registry.auth.oauth.flow_state_manager import FlowStateManager
from registry.schemas.oauth_schema import OAuthProtectedResourceMetadata


class TestCreateFlowMetadataResourceResolution:
    """Tests for resource_metadata population in create_flow_metadata.

    The resource URL is sourced (in order of precedence) from:
    1. oauth_config["resource"]
    2. oauth_config["additional_params"]["resource"]
    3. None — resource_metadata is left as None
    """

    @pytest.fixture
    def manager(self):
        return FlowStateManager(fallback_to_memory=True)

    def _base_oauth_config(self, **overrides) -> dict:
        config: dict = {
            "client_id": "test_client_id",
            "client_secret": "test_secret",
            "authorization_url": "https://example.com/oauth/authorize",
            "token_url": "https://example.com/oauth/token",
        }
        config.update(overrides)
        return config

    def _call(self, manager: FlowStateManager, oauth_config: dict):
        return manager.create_flow_metadata(
            server_name="test-server",
            server_path="/test",
            server_id="server_123",
            user_id="user_456",
            authorization_url="https://example.com/oauth/authorize",
            code_verifier="code_verifier_value",
            oauth_config=oauth_config,
            flow_id="flow_789",
        )

    # ------------------------------------------------------------------
    # Happy-path: resource resolved
    # ------------------------------------------------------------------

    def test_resource_from_top_level_config_key(self, manager):
        """resource_metadata is set when oauth_config contains a top-level 'resource' key."""
        metadata = self._call(manager, self._base_oauth_config(resource="https://mcp.hubspot.com"))

        assert isinstance(metadata.resource_metadata, OAuthProtectedResourceMetadata)
        assert metadata.resource_metadata.resource == "https://mcp.hubspot.com"

    def test_resource_from_additional_params(self, manager):
        """resource_metadata is set when 'resource' is nested inside additional_params."""
        metadata = self._call(
            manager,
            self._base_oauth_config(additional_params={"resource": "https://mcp.example.com"}),
        )

        assert isinstance(metadata.resource_metadata, OAuthProtectedResourceMetadata)
        assert metadata.resource_metadata.resource == "https://mcp.example.com"

    def test_top_level_key_takes_precedence_over_additional_params(self, manager):
        """When both sources provide a resource URL, the top-level key wins."""
        config = self._base_oauth_config(
            resource="https://primary.example.com",
            additional_params={"resource": "https://secondary.example.com"},
        )
        metadata = self._call(manager, config)

        assert metadata.resource_metadata.resource == "https://primary.example.com"

    # ------------------------------------------------------------------
    # No resource configured
    # ------------------------------------------------------------------

    def test_resource_metadata_is_none_when_not_configured(self, manager):
        """resource_metadata is None when neither config source contains a resource URL."""
        metadata = self._call(manager, self._base_oauth_config())

        assert metadata.resource_metadata is None

    def test_resource_metadata_is_none_when_additional_params_empty(self, manager):
        """resource_metadata is None when additional_params exists but has no 'resource' key."""
        metadata = self._call(manager, self._base_oauth_config(additional_params={"some_other": "val"}))

        assert metadata.resource_metadata is None

    def test_resource_metadata_is_none_when_resource_is_empty_string(self, manager):
        """An empty-string resource is treated as falsy and results in None."""
        metadata = self._call(manager, self._base_oauth_config(resource=""))

        assert metadata.resource_metadata is None
