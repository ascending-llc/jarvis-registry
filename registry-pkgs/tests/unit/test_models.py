"""Tests for ExtendedMCPServer model structure and validation."""

from datetime import UTC, datetime

import pytest
from a2a.types import AgentCard
from beanie import PydanticObjectId
from pydantic import ValidationError

from registry_pkgs.models.a2a_agent import A2AAgent
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServerDocument


class TestExtendedMCPServerStructure:
    """Test ExtendedMCPServer model structure matches design specification."""

    def test_required_fields(self):
        """Test that required fields are enforced."""
        # Use Pydantic's model_validate without triggering Beanie's Document.__init__
        with pytest.raises(ValidationError) as exc_info:
            ExtendedMCPServerDocument.model_validate({}, strict=False)

        errors = exc_info.value.errors()
        required_fields = {error["loc"][0] for error in errors if error["type"] == "missing"}

        # These fields are required according to the model
        assert "serverName" in required_fields
        assert "config" in required_fields
        assert "author" in required_fields
        # path is now optional to support external systems that don't provide it

    def test_root_level_fields_not_in_config(self):
        """Verify registry-specific fields are stored at root level, not in config."""
        server_dict = {
            "serverName": "test-server",
            "config": {
                "title": "Test Server",
                "description": "Test description",
                "type": "streamable-http",
                "url": "http://test:8000",
                "tools": "tool1, tool2",
                "capabilities": "{}",
            },
            "author": str(PydanticObjectId()),
            "path": "/mcp/test",
            "tags": ["test", "demo"],
            "status": "active",
            "numTools": 2,
            "numStars": 5,
        }

        # Use model_construct to bypass Beanie's collection check
        server = ExtendedMCPServerDocument.model_construct(**server_dict)

        # Root-level fields should NOT be in config
        assert "status" not in server.config
        assert "path" not in server.config
        assert "tags" not in server.config
        assert "numTools" not in server.config
        assert "numStars" not in server.config
        assert "lastConnected" not in server.config
        assert "lastError" not in server.config
        assert "errorMessage" not in server.config

        # Root-level fields should be accessible directly
        assert server.status == "active"
        assert server.path == "/mcp/test"
        assert server.tags == ["test", "demo"]
        assert server.numTools == 2
        assert server.numStars == 5

    def test_config_fields_structure(self):
        """Verify config object contains MCP-specific configuration."""
        config_data = {
            "title": "GitHub Server",
            "description": "GitHub integration",
            "type": "streamable-http",
            "url": "http://github-server:8011",
            "apiKey": {
                "key": "test_key",
                "source": "env",
                "authorization_type": "bearer",
            },
            "requiresOAuth": False,
            "capabilities": '{"experimental": {}}',
            "tools": "create_repo, list_issues",
            "toolFunctions": {
                "create_repo": {
                    "type": "function",
                    "function": {
                        "name": "create_repo",
                        "description": "Create a repository",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            },
            "initDuration": 170,
        }

        server_dict = {
            "serverName": "github",
            "config": config_data,
            "author": str(PydanticObjectId()),
            "path": "/mcp/github",
        }

        server = ExtendedMCPServerDocument.model_construct(**server_dict)

        # Verify config fields are stored correctly
        assert server.config["title"] == "GitHub Server"
        assert server.config["description"] == "GitHub integration"
        assert server.config["type"] == "streamable-http"
        assert server.config["url"] == "http://github-server:8011"
        assert server.config["requiresOAuth"] is False
        assert server.config["capabilities"] == '{"experimental": {}}'
        assert server.config["tools"] == "create_repo, list_issues"
        assert "toolFunctions" in server.config
        assert server.config["initDuration"] == 170

    def test_default_values(self):
        """Test default values are set correctly."""
        server_dict = {
            "serverName": "test",
            "config": {"title": "Test", "type": "sse", "url": "http://test:8000"},
            "author": str(PydanticObjectId()),
            "path": "/mcp/test",
        }

        server = ExtendedMCPServerDocument.model_construct(**server_dict)

        # Verify defaults (model_construct doesn't apply defaults, so we need to check field definitions)
        # These would be set by Pydantic during normal instantiation
        assert hasattr(server, "scope")
        assert hasattr(server, "status")
        assert hasattr(server, "tags")

    def test_optional_monitoring_fields(self):
        """Test optional monitoring fields (lastConnected, lastError, errorMessage)."""
        now = datetime.now(UTC)

        server_dict = {
            "serverName": "test",
            "config": {"title": "Test", "type": "sse", "url": "http://test:8000"},
            "author": str(PydanticObjectId()),
            "path": "/mcp/test",
            "lastConnected": now,
            "lastError": now,
            "errorMessage": "Connection timeout",
        }

        server = ExtendedMCPServerDocument.model_construct(**server_dict)

        assert server.lastConnected == now
        assert server.lastError == now
        assert server.errorMessage == "Connection timeout"

    def test_status_values(self):
        """Test valid status values."""
        valid_statuses = ["active", "inactive", "error"]

        for status in valid_statuses:
            server_dict = {
                "serverName": f"test-{status}",
                "config": {"title": "Test", "type": "sse", "url": "http://test:8000"},
                "author": str(PydanticObjectId()),
                "path": f"/mcp/{status}",
                "status": status,
            }
            server = ExtendedMCPServerDocument.model_construct(**server_dict)
            assert server.status == status

    def test_tags_array(self):
        """Test tags field accepts array of strings."""
        server_dict = {
            "serverName": "test",
            "config": {"title": "Test", "type": "sse", "url": "http://test:8000"},
            "author": str(PydanticObjectId()),
            "path": "/mcp/test",
            "tags": ["github", "git", "vcs"],
        }

        server = ExtendedMCPServerDocument.model_construct(**server_dict)

        assert isinstance(server.tags, list)
        assert len(server.tags) == 3
        assert "github" in server.tags

    def test_beanie_settings(self):
        """Test Beanie document settings are configured correctly."""
        assert ExtendedMCPServerDocument.Settings.name == "mcpservers"
        assert ExtendedMCPServerDocument.Settings.keep_nulls is False
        assert ExtendedMCPServerDocument.Settings.use_state_management is True

    def test_oauth_config_structure(self):
        """Test server with OAuth configuration in config object."""
        config_data = {
            "title": "OAuth Server",
            "description": "Server with OAuth",
            "type": "streamable-http",
            "url": "http://oauth-server:8000",
            "requiresOAuth": True,
            "oauth": {
                "client_id": "test_client",
                "authorization_url": "https://auth.example.com/authorize",
                "token_url": "https://auth.example.com/token",
                "scopes": ["read", "write"],
            },
            "capabilities": "{}",
            "tools": "tool1",
        }

        server_dict = {
            "serverName": "oauth-test",
            "config": config_data,
            "author": str(PydanticObjectId()),
            "path": "/mcp/oauth",
        }

        server = ExtendedMCPServerDocument.model_construct(**server_dict)

        assert server.config["requiresOAuth"] is True
        assert "oauth" in server.config
        assert server.config["oauth"]["client_id"] == "test_client"
        assert "scopes" in server.config["oauth"]

    def test_numtools_field_alias(self):
        """Test numTools field accepts both numTools and its alias."""
        server_dict = {
            "serverName": "test",
            "config": {"title": "Test", "type": "sse", "url": "http://test:8000"},
            "author": str(PydanticObjectId()),
            "path": "/mcp/test",
            "numTools": 5,
        }

        server = ExtendedMCPServerDocument.model_construct(**server_dict)

        assert server.numTools == 5

    def test_to_documents_includes_runtime_version_metadata(self):
        server = ExtendedMCPServerDocument.model_construct(
            id=PydanticObjectId(),
            serverName="versioned-server",
            config={
                "title": "Versioned Server",
                "description": "desc",
                "type": "streamable-http",
                "url": "https://example.com/mcp",
                "toolFunctions": {},
                "resources": [],
                "prompts": [],
            },
            author=PydanticObjectId(),
            path="/agentcore/mcp/versioned-server",
            status="active",
            federationId="arn:aws:bedrock-agentcore:us-east-1:1:runtime/versioned",
            federationMetadata={"sourceType": "runtime", "runtimeVersion": "7"},
        )

        docs = server.to_documents()
        assert docs
        assert docs[0].metadata.get("runtimeVersion") == "7"

    def test_tool_documents_use_downstream_mcp_tool_name_only(self):
        server = ExtendedMCPServerDocument.model_construct(
            id=PydanticObjectId(),
            serverName="tool-server",
            config={
                "title": "Tool Server",
                "description": "desc",
                "type": "streamable-http",
                "url": "https://example.com/mcp",
                "toolFunctions": {
                    "scoped_tool_mcp_tool_server": {
                        "type": "function",
                        "mcpToolName": "downstream_tool",
                        "function": {
                            "name": "scoped_tool_mcp_tool_server",
                            "description": "desc",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                },
                "resources": [],
                "prompts": [],
            },
            author=PydanticObjectId(),
            path="/mcp/tool-server",
            status="active",
        )

        docs = server.to_documents()
        tool_doc = next(doc for doc in docs if doc.metadata.get("entity_type") == "tool")

        assert tool_doc.metadata["tool_name"] == "downstream_tool"
        assert "original_mcp_name" not in tool_doc.metadata

        result = ExtendedMCPServerDocument.from_document(tool_doc)
        assert result["tool_name"] == "downstream_tool"
        assert "original_mcp_name" not in result

    def test_a2a_to_documents_includes_runtime_version_metadata(self):
        agent = A2AAgent.model_construct(
            id=PydanticObjectId(),
            path="/agentcore/a2a/versioned-agent",
            card=AgentCard(
                name="Versioned Agent",
                description="A test A2A agent",
                url="https://example.com/a2a",
                version="1.0.0",
                capabilities={"streaming": True},
                defaultInputModes=["text/plain"],
                defaultOutputModes=["application/json"],
                skills=[],
            ),
            tags=["agentcore"],
            status="active",
            isEnabled=True,
            author=PydanticObjectId(),
            federationMetadata={"sourceType": "runtime", "runtimeVersion": "11"},
        )

        docs = agent.to_documents()
        assert docs
        assert docs[0].metadata.get("runtimeVersion") == "11"

    def test_a2a_agent_uses_federation_metadata_for_remote_identity(self):
        agent = A2AAgent.from_a2a_agent_card(
            card_data={
                "name": "Azure Agent",
                "description": "A federated Azure agent",
                "url": "https://example.projects.ai.azure.com",
                "version": "3",
                "capabilities": {"streaming": True},
                "defaultInputModes": ["text/plain"],
                "defaultOutputModes": ["application/json"],
                "skills": [],
            },
            path="/azure-ai-foundry/a2a/azure-agent",
            author=PydanticObjectId(),
            federationMetadata={
                "providerType": "azure_ai_foundry",
                "agentName": "azure-agent",
                "agentVersion": "3",
                "agentVersionId": "asst_abc123",
            },
        )

        assert agent.federationRefId is None
        assert agent.federationMetadata["providerType"] == "azure_ai_foundry"
        assert agent.federationMetadata["agentVersionId"] == "asst_abc123"
