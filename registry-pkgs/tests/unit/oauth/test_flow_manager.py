import base64
import json
import time
from typing import Any
from uuid import UUID

import pytest

from registry_pkgs.oauth.flow_state_manager import FlowStateManager
from registry_pkgs.oauth.schemas import OAuthFlowStatus
from registry_pkgs.oauth.types import ClientBranding, StateMetadata
from tests.fake_redis import FakeRedis


@pytest.mark.unit
class TestFlowStateManager:
    """Test suite for FlowStateManager."""

    def test_init_default_values(self):
        """Test initialization with default values."""
        manager = FlowStateManager(FakeRedis(), redis_key_prefix="jarvis-registry")

        assert manager._flow_ttl == 600
        assert not hasattr(manager, "_memory_flows")
        assert manager.DEFAULT_FLOW_TTL == 600

    def test_init_fallback_to_memory(self):
        """Test initialization with memory fallback."""
        manager = FlowStateManager(FakeRedis(), redis_key_prefix="jarvis-registry")

        # Should initialize with memory storage when Redis is unavailable
        assert not hasattr(manager, "_memory_flows")

    def test_generate_flow_id(self):
        """Test flow ID generation."""
        manager = FlowStateManager(FakeRedis(), redis_key_prefix="jarvis-registry")
        user_id = "test-user"
        server_name = "test-server"

        flow_id = manager.generate_flow_id(user_id, server_name)

        # Check format: user_id:server_name
        assert flow_id == f"{user_id}:{server_name}"

    def test_encode_decode_state(self) -> None:
        """Test encoding and decoding state parameter."""
        manager = FlowStateManager(FakeRedis(), redis_key_prefix="jarvis-registry")

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
        state_metadata: StateMetadata = {
            "client_branding": ClientBranding.VSCODE,
        }
        state = manager.encode_state(flow_id, state_metadata=state_metadata)
        state_dict = manager.decode_state(state)
        elicitation_id = state_dict["meta"]["elicitation_id"]

        assert state_dict["flow_id"] == flow_id
        assert isinstance(state_dict["security_token"], str)
        assert state_dict["meta"]["client_branding"] == ClientBranding.VSCODE
        assert UUID(elicitation_id).version == 4

    def _prepare_wrong_state(self, dict_: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(dict_).encode("utf-8")).decode("utf-8").rstrip("=")

    def test_decode_state_invalid_format(self):
        """Test decoding invalid state format."""
        manager = FlowStateManager(FakeRedis(), redis_key_prefix="jarvis-registry")

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
        manager = FlowStateManager(FakeRedis(), redis_key_prefix="jarvis-registry")

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
            redirect_uri="https://example.com/api/v1/mcp/test-server/oauth/callback",
            state_metadata=state_metadata,
            device_code="device-1",
        )

        assert metadata.server_name == server_name
        assert metadata.user_id == user_id
        assert metadata.authorization_url == authorization_url
        assert metadata.code_verifier == code_verifier
        assert metadata.client_info.client_id == "test-client-id"
        assert metadata.device_code == "device-1"

        state_dict = manager.decode_state(metadata.state)
        elicitation_id = state_dict["meta"]["elicitation_id"]

        assert state_dict["flow_id"] == flow_id
        assert isinstance(state_dict["security_token"], str)
        assert state_dict["meta"]["client_branding"] == branding
        assert UUID(elicitation_id).version == 4

    @pytest.mark.asyncio
    async def test_create_flow_success(self):
        """Test successful flow creation."""
        manager = FlowStateManager(FakeRedis(), redis_key_prefix="jarvis-registry")
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
            redirect_uri="https://example.com/api/v1/mcp/test-server/oauth/callback",
        )

        flow = await manager.create_flow(flow_id, server_id, user_id, code_verifier, metadata)

        assert flow.flow_id == flow_id
        assert flow.server_name == server_name
        assert flow.user_id == user_id
        assert flow.status == OAuthFlowStatus.PENDING

    @pytest.mark.asyncio
    async def test_get_flow_found(self):
        """Test retrieving an existing flow."""
        manager = FlowStateManager(FakeRedis(), redis_key_prefix="jarvis-registry")
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
            redirect_uri="https://example.com/api/v1/mcp/test-server/oauth/callback",
        )
        await manager.create_flow(flow_id, server_id, user_id, code_verifier, metadata)

        # Retrieve flow
        flow = await manager.get_flow(flow_id)

        assert flow is not None
        assert flow.flow_id == flow_id
        assert flow.status == OAuthFlowStatus.PENDING

    @pytest.mark.asyncio
    async def test_get_flow_not_found(self):
        """Test retrieving a non-existent flow."""
        manager = FlowStateManager(FakeRedis(), redis_key_prefix="jarvis-registry")
        flow_id = "non-existent-flow"

        result = await manager.get_flow(flow_id)

        assert result is None

    @pytest.mark.asyncio
    async def test_is_flow_expired(self):
        """Test checking if flow is expired."""
        manager = FlowStateManager(FakeRedis(), redis_key_prefix="jarvis-registry")
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
            redirect_uri="https://example.com/api/v1/mcp/test-server/oauth/callback",
        )
        flow = await manager.create_flow(flow_id, server_id, user_id, code_verifier, metadata)

        # Should not be expired immediately
        assert not manager.is_flow_expired(flow)

        # Simulate expired flow
        flow.created_at = time.time() - 700  # More than DEFAULT_FLOW_TTL
        assert manager.is_flow_expired(flow)

    @pytest.mark.asyncio
    async def test_complete_flow_success(self):
        """Test successfully completing a flow."""
        manager = FlowStateManager(FakeRedis(), redis_key_prefix="jarvis-registry")
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
            redirect_uri="https://example.com/api/v1/mcp/test-server/oauth/callback",
        )
        await manager.create_flow(flow_id, server_id, user_id, code_verifier, metadata)

        # Complete the flow
        from registry_pkgs.oauth.schemas import OAuthTokens

        tokens = OAuthTokens(access_token="test-token", token_type="Bearer", expires_in=3600)
        await manager.complete_flow(flow_id, tokens)

        # Verify the flow was updated
        flow = await manager.get_flow(flow_id)
        assert flow is not None
        assert flow.status == OAuthFlowStatus.COMPLETED
        assert flow.tokens == tokens

    @pytest.mark.asyncio
    async def test_complete_flow_not_found(self):
        """Test completing a non-existent flow."""
        manager = FlowStateManager(FakeRedis(), redis_key_prefix="jarvis-registry")
        flow_id = "non-existent-flow"

        from registry_pkgs.oauth.schemas import OAuthTokens

        tokens = OAuthTokens(access_token="test-token", token_type="Bearer", expires_in=3600)

        # Should not raise an exception
        with pytest.raises(ValueError, match="not found"):
            await manager.complete_flow(flow_id, tokens)

    @pytest.mark.asyncio
    async def test_fail_flow_success(self):
        """Test successfully failing a flow."""
        manager = FlowStateManager(FakeRedis(), redis_key_prefix="jarvis-registry")
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
            redirect_uri="https://example.com/api/v1/mcp/test-server/oauth/callback",
        )
        await manager.create_flow(flow_id, server_id, user_id, code_verifier, metadata)

        # Fail the flow
        await manager.fail_flow(flow_id, error_message)

        # Verify the flow was updated
        flow = await manager.get_flow(flow_id)
        assert flow is not None
        assert flow.status == OAuthFlowStatus.FAILED
        assert flow.error == error_message

    @pytest.mark.asyncio
    async def test_fail_flow_not_found(self):
        """Test failing a non-existent flow."""
        manager = FlowStateManager(FakeRedis(), redis_key_prefix="jarvis-registry")
        flow_id = "non-existent-flow"
        error_message = "Authentication failed"

        # Should not raise an exception
        with pytest.raises(ValueError, match="not found"):
            await manager.fail_flow(flow_id, error_message)

    @pytest.mark.asyncio
    async def test_delete_flow_success(self):
        """Test successfully deleting a flow."""
        manager = FlowStateManager(FakeRedis(), redis_key_prefix="jarvis-registry")
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
            redirect_uri="https://example.com/api/v1/mcp/test-server/oauth/callback",
        )
        await manager.create_flow(flow_id, server_id, user_id, code_verifier, metadata)

        # Verify it exists
        assert await manager.get_flow(flow_id) is not None

        # Delete the flow
        await manager.delete_flow(flow_id)

        # Verify it's deleted
        assert await manager.get_flow(flow_id) is None

    @pytest.mark.asyncio
    async def test_cancel_user_flow(self):
        """Test cancelling a user flow."""
        manager = FlowStateManager(FakeRedis(), redis_key_prefix="jarvis-registry")
        flow_id = "test-user:test-server"
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
            redirect_uri="https://example.com/api/v1/mcp/test-server/oauth/callback",
        )
        await manager.create_flow(flow_id, server_id, user_id, code_verifier, metadata)

        # Cancel the flow
        result = await manager.cancel_user_flow(user_id, server_id)

        assert result is True

        # Verify flow is marked as failed
        flow = await manager.get_flow(flow_id)
        assert flow.status == OAuthFlowStatus.FAILED

    @pytest.mark.asyncio
    async def test_get_user_flows(self):
        """Test getting user flows."""
        manager = FlowStateManager(FakeRedis(), redis_key_prefix="jarvis-registry")
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

        for _i in range(3):
            flow_id = "test-user:test-server"
            metadata = manager.create_flow_metadata(
                server_name,
                "/test-server",
                server_id,
                user_id,
                "https://example.com/auth",
                code_verifier,
                oauth_config,
                flow_id,
                redirect_uri="https://example.com/api/v1/mcp/test-server/oauth/callback",
            )
            await manager.create_flow(flow_id, server_id, user_id, code_verifier, metadata)

        # Get user flows
        flows = await manager.get_user_flows(user_id, server_id)

        assert len(flows) == 1
        for flow in flows:
            assert flow.user_id == user_id
            assert flow.server_name == server_name

    @pytest.mark.asyncio
    async def test_cleanup_expired_flows(self):
        """Test cleanup of expired flows."""
        manager = FlowStateManager(FakeRedis(), redis_key_prefix="jarvis-registry")

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
                redirect_uri="https://example.com/api/v1/mcp/test-server/oauth/callback",
            )
            flow = await manager.create_flow(flow_id, server_id, user_id, code_verifier, metadata)
            # Make first flow expired
            if i == 0:
                flow.created_at = time.time() - 700
                manager._redis_storage.save_flow(flow)
            flows.append(flow_id)

        # Clean up expired flows
        cleaned = await manager.cleanup_expired_flows()

        # Should clean up at least 1 expired flow
        assert cleaned >= 1


@pytest.mark.unit
class TestFlowStateManagerIntegration:
    """Integration tests for FlowStateManager."""

    @pytest.mark.asyncio
    async def test_full_flow_lifecycle(self):
        """Test a complete OAuth flow lifecycle."""
        manager = FlowStateManager(FakeRedis(), redis_key_prefix="jarvis-registry")
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
            redirect_uri="https://example.com/api/v1/mcp/test-server/oauth/callback",
        )
        await manager.create_flow(flow_id, server_id, user_id, code_verifier, metadata)

        # Verify flow exists and is pending
        flow = await manager.get_flow(flow_id)
        assert flow is not None
        assert flow.status == OAuthFlowStatus.PENDING

        # Complete the flow
        from registry_pkgs.oauth.schemas import OAuthTokens

        tokens = OAuthTokens(
            access_token="test-access-token", refresh_token="test-refresh-token", token_type="Bearer", expires_in=3600
        )
        await manager.complete_flow(flow_id, tokens)

        # Verify flow is completed
        completed_flow = await manager.get_flow(flow_id)
        assert completed_flow.status == OAuthFlowStatus.COMPLETED
        assert completed_flow.tokens.access_token == "test-access-token"

        # Delete the flow
        await manager.delete_flow(flow_id)

        # Verify flow is deleted
        assert await manager.get_flow(flow_id) is None

    @pytest.mark.asyncio
    async def test_concurrent_flows(self):
        """Test handling multiple concurrent flows."""
        manager = FlowStateManager(FakeRedis(), redis_key_prefix="jarvis-registry")

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
                redirect_uri="https://example.com/api/v1/mcp/test-server/oauth/callback",
            )
            await manager.create_flow(flow_id, server_id, user_id, code_verifier, metadata)
            flows.append(flow_id)

        # Verify all flows exist
        for flow_id in flows:
            flow = await manager.get_flow(flow_id)
            assert flow is not None
            assert flow.flow_id == flow_id

        # Delete all flows
        for flow_id in flows:
            await manager.delete_flow(flow_id)

        # Verify all flows are deleted
        for flow_id in flows:
            assert await manager.get_flow(flow_id) is None
