import base64
import json
import time
from typing import Any
from uuid import UUID

import pytest

from registry.auth.oauth.flow_state_manager import FlowStateManager
from registry.auth.oauth.types import ClientBranding
from registry.schemas.enums import OAuthFlowStatus


@pytest.mark.unit
class TestFlowStateManager:
    """Test suite for FlowStateManager."""

    def test_init_default_values(self):
        """Test initialization with default values."""
        manager = FlowStateManager()

        assert manager._flow_ttl == 600
        assert manager._memory_flows == {}
        assert manager.DEFAULT_FLOW_TTL == 600

    def test_init_fallback_to_memory(self):
        """Test initialization with memory fallback."""
        manager = FlowStateManager(fallback_to_memory=True)

        # Should initialize with memory storage when Redis is unavailable
        assert manager._memory_flows is not None

    def test_generate_flow_id(self):
        """Test flow ID generation."""
        manager = FlowStateManager()
        user_id = "test-user"
        server_name = "test-server"

        flow_id = manager.generate_flow_id(user_id, server_name)

        # Check format: user_id:server_name
        assert flow_id == f"{user_id}:{server_name}"

    def test_encode_decode_state(self):
        """Test encoding and decoding state parameter."""
        manager = FlowStateManager()

        # Encode/decode with flow_id
        flow_id = "test-flow-id"
        state = manager.encode_state(flow_id)
        state_dict = manager.decode_state(state)

        assert state_dict["flow_id"] == flow_id
        assert isinstance(state_dict["security_token"], str)
        assert "meta" not in state_dict

        # Encode/decode with flow_id and security_token
        flow_id = "test-flow-id"
        security_token = "1234567890"
        state = manager.encode_state(flow_id, security_token)
        state_dict = manager.decode_state(state)

        assert state_dict["flow_id"] == flow_id
        assert state_dict["security_token"] == security_token
        assert "meta" not in state_dict

        # Encode/decode with flow_id and state_metadata
        flow_id = "test-flow-id"
        state_metadata = {"client_branding": ClientBranding.CLAUDE}
        state = manager.encode_state(flow_id, state_metadata=state_metadata)
        state_dict = manager.decode_state(state)
        elicitation_id = state_dict["meta"]["elicitation_id"]

        assert state_dict["flow_id"] == flow_id
        assert isinstance(state_dict["security_token"], str)
        assert state_dict["meta"]["client_branding"] == ClientBranding.CLAUDE
        assert UUID(elicitation_id).version == 4

    def _prepare_wrong_state(self, dict_: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(dict_).encode("utf-8")).decode("utf-8").rstrip("=")

    def test_decode_state_invalid_format(self):
        """Test decoding invalid state format."""
        manager = FlowStateManager()

        with pytest.raises(ValueError, match="state is not valid base64url encoded JSON string"):
            manager.decode_state("not encoded JSON string")

        with pytest.raises(ValueError, match="flow_id key is not in"):
            manager.decode_state(self._prepare_wrong_state({"security_token": "1234567890"}))

        with pytest.raises(ValueError, match="security_token key is not in"):
            manager.decode_state(
                self._prepare_wrong_state({"flow_id": "abc123", "meta": {"client_branding": "vscode"}})
            )

    def test_create_flow_metadata(self):
        """Test creating flow metadata."""
        manager = FlowStateManager()

        server_name = "test-server"
        server_path = "/test-server"
        server_id = "test-server"
        user_id = "test-user"
        authorization_url = "https://example.com/oauth/authorize"
        code_verifier = "test-verifier"
        flow_id = "test-flow-id"
        oauth_config = {
            "client_id": "test-client-id",
            "client_secret": "test-secret",
            "scope": ["read", "write"],
            "authorization_url": authorization_url,
            "token_url": "https://example.com/oauth/token",
        }

        branding = ClientBranding.CURSOR
        state_metadata = {"client_branding": branding}

        metadata = manager.create_flow_metadata(
            server_name,
            server_path,
            server_id,
            user_id,
            authorization_url,
            code_verifier,
            oauth_config,
            flow_id,
            state_metadata=state_metadata,
        )

        assert metadata.server_name == server_name
        assert metadata.user_id == user_id
        assert metadata.authorization_url == authorization_url
        assert metadata.code_verifier == code_verifier
        assert metadata.client_info.client_id == "test-client-id"

        state_dict = manager.decode_state(metadata.state)
        elicitation_id = state_dict["meta"]["elicitation_id"]

        assert state_dict["flow_id"] == flow_id
        assert isinstance(state_dict["security_token"], str)
        assert state_dict["meta"]["client_branding"] == branding
        assert UUID(elicitation_id).version == 4

    def test_create_flow_success(self):
        """Test successful flow creation."""
        manager = FlowStateManager()
        flow_id = "test-flow-id"
        server_name = "test-server"
        server_id = "test-server"
        user_id = "test-user"
        code_verifier = "test-verifier"

        # Create minimal metadata
        oauth_config = {
            "client_id": "test-client",
            "scope": ["read"],
            "authorization_url": "https://example.com/auth",
            "token_url": "https://example.com/token",
        }
        metadata = manager.create_flow_metadata(
            server_name,
            "/test-server",
            server_id,
            user_id,
            "https://example.com/auth",
            code_verifier,
            oauth_config,
            flow_id,
        )

        flow = manager.create_flow(flow_id, server_id, user_id, code_verifier, metadata)

        assert flow.flow_id == flow_id
        assert flow.server_name == server_name
        assert flow.user_id == user_id
        assert flow.status == OAuthFlowStatus.PENDING

    def test_get_flow_found(self):
        """Test retrieving an existing flow."""
        manager = FlowStateManager()
        flow_id = "test-flow-id"
        server_name = "test-server"
        server_id = "test-server"
        user_id = "test-user"
        code_verifier = "test-verifier"

        # Create flow
        oauth_config = {
            "client_id": "test-client",
            "scope": ["read"],
            "authorization_url": "https://example.com/auth",
            "token_url": "https://example.com/token",
        }
        metadata = manager.create_flow_metadata(
            server_name,
            "/test-server",
            server_id,
            user_id,
            "https://example.com/auth",
            code_verifier,
            oauth_config,
            flow_id,
        )
        manager.create_flow(flow_id, server_id, user_id, code_verifier, metadata)

        # Retrieve flow
        flow = manager.get_flow(flow_id)

        assert flow is not None
        assert flow.flow_id == flow_id
        assert flow.status == OAuthFlowStatus.PENDING

    def test_get_flow_not_found(self):
        """Test retrieving a non-existent flow."""
        manager = FlowStateManager()
        flow_id = "non-existent-flow"

        result = manager.get_flow(flow_id)

        assert result is None

    def test_is_flow_expired(self):
        """Test checking if flow is expired."""
        manager = FlowStateManager()
        flow_id = "test-flow-id"
        server_name = "test-server"
        server_id = "test-server"
        user_id = "test-user"
        code_verifier = "test-verifier"

        # Create flow
        oauth_config = {
            "client_id": "test-client",
            "scope": ["read"],
            "authorization_url": "https://example.com/auth",
            "token_url": "https://example.com/token",
        }
        metadata = manager.create_flow_metadata(
            server_name,
            "/test-server",
            server_id,
            user_id,
            "https://example.com/auth",
            code_verifier,
            oauth_config,
            flow_id,
        )
        flow = manager.create_flow(flow_id, server_id, user_id, code_verifier, metadata)

        # Should not be expired immediately
        assert not manager.is_flow_expired(flow)

        # Simulate expired flow
        flow.created_at = time.time() - 700  # More than DEFAULT_FLOW_TTL
        assert manager.is_flow_expired(flow)

    def test_complete_flow_success(self):
        """Test successfully completing a flow."""
        manager = FlowStateManager()
        flow_id = "test-flow-id"
        server_name = "test-server"
        server_id = "test-server"
        user_id = "test-user"
        code_verifier = "test-verifier"

        # Create flow
        oauth_config = {
            "client_id": "test-client",
            "scope": ["read"],
            "authorization_url": "https://example.com/auth",
            "token_url": "https://example.com/token",
        }
        metadata = manager.create_flow_metadata(
            server_name,
            "/test-server",
            server_id,
            user_id,
            "https://example.com/auth",
            code_verifier,
            oauth_config,
            flow_id,
        )
        manager.create_flow(flow_id, server_id, user_id, code_verifier, metadata)

        # Complete the flow
        from registry.schemas.oauth_schema import OAuthTokens

        tokens = OAuthTokens(access_token="test-token", token_type="Bearer", expires_in=3600)
        manager.complete_flow(flow_id, tokens)

        # Verify the flow was updated
        flow = manager.get_flow(flow_id)
        assert flow is not None
        assert flow.status == OAuthFlowStatus.COMPLETED
        assert flow.tokens == tokens

    def test_complete_flow_not_found(self):
        """Test completing a non-existent flow."""
        manager = FlowStateManager()
        flow_id = "non-existent-flow"

        from registry.schemas.oauth_schema import OAuthTokens

        tokens = OAuthTokens(access_token="test-token", token_type="Bearer", expires_in=3600)

        # Should not raise an exception
        manager.complete_flow(flow_id, tokens)

    def test_fail_flow_success(self):
        """Test successfully failing a flow."""
        manager = FlowStateManager()
        flow_id = "test-flow-id"
        server_name = "test-server"
        server_id = "test-server"
        user_id = "test-user"
        code_verifier = "test-verifier"
        error_message = "Authentication failed"

        # Create flow
        oauth_config = {
            "client_id": "test-client",
            "scope": ["read"],
            "authorization_url": "https://example.com/auth",
            "token_url": "https://example.com/token",
        }
        metadata = manager.create_flow_metadata(
            server_name,
            "/test-server",
            server_id,
            user_id,
            "https://example.com/auth",
            code_verifier,
            oauth_config,
            flow_id,
        )
        manager.create_flow(flow_id, server_id, user_id, code_verifier, metadata)

        # Fail the flow
        manager.fail_flow(flow_id, error_message)

        # Verify the flow was updated
        flow = manager.get_flow(flow_id)
        assert flow is not None
        assert flow.status == OAuthFlowStatus.FAILED
        assert flow.error == error_message

    def test_fail_flow_not_found(self):
        """Test failing a non-existent flow."""
        manager = FlowStateManager()
        flow_id = "non-existent-flow"
        error_message = "Authentication failed"

        # Should not raise an exception
        manager.fail_flow(flow_id, error_message)

    def test_delete_flow_success(self):
        """Test successfully deleting a flow."""
        manager = FlowStateManager()
        flow_id = "test-flow-id"
        server_name = "test-server"
        server_id = "test-server"
        user_id = "test-user"
        code_verifier = "test-verifier"

        # Create flow
        oauth_config = {
            "client_id": "test-client",
            "scope": ["read"],
            "authorization_url": "https://example.com/auth",
            "token_url": "https://example.com/token",
        }
        metadata = manager.create_flow_metadata(
            server_name,
            "/test-server",
            server_id,
            user_id,
            "https://example.com/auth",
            code_verifier,
            oauth_config,
            flow_id,
        )
        manager.create_flow(flow_id, server_id, user_id, code_verifier, metadata)

        # Verify it exists
        assert manager.get_flow(flow_id) is not None

        # Delete the flow
        manager.delete_flow(flow_id)

        # Verify it's deleted
        assert manager.get_flow(flow_id) is None

    def test_cancel_user_flow(self):
        """Test cancelling a user flow."""
        manager = FlowStateManager()
        flow_id = "test-flow-id"
        server_name = "test-server"
        server_id = "test-server"
        user_id = "test-user"
        code_verifier = "test-verifier"

        # Create flow
        oauth_config = {
            "client_id": "test-client",
            "scope": ["read"],
            "authorization_url": "https://example.com/auth",
            "token_url": "https://example.com/token",
        }
        metadata = manager.create_flow_metadata(
            server_name,
            "/test-server",
            server_id,
            user_id,
            "https://example.com/auth",
            code_verifier,
            oauth_config,
            flow_id,
        )
        manager.create_flow(flow_id, server_id, user_id, code_verifier, metadata)

        # Cancel the flow
        result = manager.cancel_user_flow(user_id, server_id)

        assert result is True

        # Verify flow is marked as failed
        flow = manager.get_flow(flow_id)
        assert flow.status == OAuthFlowStatus.FAILED

    def test_get_user_flows(self):
        """Test getting user flows."""
        manager = FlowStateManager()
        server_name = "test-server"
        server_id = "test-server"
        user_id = "test-user"
        code_verifier = "test-verifier"

        # Create multiple flows for same user/server
        oauth_config = {
            "client_id": "test-client",
            "scope": ["read"],
            "authorization_url": "https://example.com/auth",
            "token_url": "https://example.com/token",
        }

        for i in range(3):
            flow_id = f"flow-{i}"
            metadata = manager.create_flow_metadata(
                server_name,
                "/test-server",
                server_id,
                user_id,
                "https://example.com/auth",
                code_verifier,
                oauth_config,
                flow_id,
            )
            manager.create_flow(flow_id, server_id, user_id, code_verifier, metadata)

        # Get user flows
        flows = manager.get_user_flows(user_id, server_id)

        assert len(flows) == 3
        for flow in flows:
            assert flow.user_id == user_id
            assert flow.server_name == server_name

    def test_singleton_get_flow_manager(self):
        """Test the singleton get_flow_manager function."""
        from registry.auth.oauth.flow_state_manager import get_flow_state_manager

        # First call should create instance
        manager1 = get_flow_state_manager()
        assert manager1 is not None

        # Second call should return same instance
        manager2 = get_flow_state_manager()
        assert manager2 is manager1

        # Verify it's a FlowStateManager instance
        assert isinstance(manager1, FlowStateManager)

    @pytest.mark.asyncio
    async def test_cleanup_expired_flows(self):
        """Test cleanup of expired flows."""
        manager = FlowStateManager()

        # Create some flows
        flows = []
        for i in range(3):
            flow_id = f"flow-{i}"
            server_name = "test-server"
            server_id = "test-server"
            user_id = "test-user"
            code_verifier = "test-verifier"

            oauth_config = {
                "client_id": "test-client",
                "scope": ["read"],
                "authorization_url": "https://example.com/auth",
                "token_url": "https://example.com/token",
            }
            metadata = manager.create_flow_metadata(
                server_name,
                "/test-server",
                server_id,
                user_id,
                "https://example.com/auth",
                code_verifier,
                oauth_config,
                flow_id,
            )
            flow = manager.create_flow(flow_id, server_id, user_id, code_verifier, metadata)
            # Make first flow expired
            if i == 0:
                flow.created_at = time.time() - 700
            flows.append(flow_id)

        # Clean up expired flows
        cleaned = await manager.cleanup_expired_flows()

        # Should clean up at least 1 expired flow
        assert cleaned >= 1


@pytest.mark.unit
class TestFlowStateManagerIntegration:
    """Integration tests for FlowStateManager."""

    def test_full_flow_lifecycle(self):
        """Test a complete OAuth flow lifecycle."""
        manager = FlowStateManager()
        user_id = "test-user"
        server_name = "test-server"
        server_id = "test-server"

        # Generate flow ID
        flow_id = manager.generate_flow_id(user_id, server_id)

        # Create flow
        code_verifier = "test-verifier"
        oauth_config = {
            "client_id": "test-client",
            "scope": ["read"],
            "authorization_url": "https://example.com/auth",
            "token_url": "https://example.com/token",
        }
        metadata = manager.create_flow_metadata(
            server_name,
            "/test-server",
            server_id,
            user_id,
            "https://example.com/auth",
            code_verifier,
            oauth_config,
            flow_id,
        )
        manager.create_flow(flow_id, server_id, user_id, code_verifier, metadata)

        # Verify flow exists and is pending
        flow = manager.get_flow(flow_id)
        assert flow is not None
        assert flow.status == OAuthFlowStatus.PENDING

        # Complete the flow
        from registry.schemas.oauth_schema import OAuthTokens

        tokens = OAuthTokens(
            access_token="test-access-token", refresh_token="test-refresh-token", token_type="Bearer", expires_in=3600
        )
        manager.complete_flow(flow_id, tokens)

        # Verify flow is completed
        completed_flow = manager.get_flow(flow_id)
        assert completed_flow.status == OAuthFlowStatus.COMPLETED
        assert completed_flow.tokens.access_token == "test-access-token"

        # Delete the flow
        manager.delete_flow(flow_id)

        # Verify flow is deleted
        assert manager.get_flow(flow_id) is None

    def test_concurrent_flows(self):
        """Test handling multiple concurrent flows."""
        manager = FlowStateManager()

        # Create multiple flows
        flows = []
        for i in range(5):
            user_id = f"user-{i}"
            server_name = f"server-{i}"
            server_id = f"server-{i}"
            flow_id = manager.generate_flow_id(user_id, server_id)
            code_verifier = "test-verifier"

            oauth_config = {
                "client_id": "test-client",
                "scope": ["read"],
                "authorization_url": "https://example.com/auth",
                "token_url": "https://example.com/token",
            }
            metadata = manager.create_flow_metadata(
                server_name,
                "/test-server",
                server_id,
                user_id,
                "https://example.com/auth",
                code_verifier,
                oauth_config,
                flow_id,
            )
            manager.create_flow(flow_id, server_id, user_id, code_verifier, metadata)
            flows.append(flow_id)

        # Verify all flows exist
        for flow_id in flows:
            flow = manager.get_flow(flow_id)
            assert flow is not None
            assert flow.flow_id == flow_id

        # Delete all flows
        for flow_id in flows:
            manager.delete_flow(flow_id)

        # Verify all flows are deleted
        for flow_id in flows:
            assert manager.get_flow(flow_id) is None
