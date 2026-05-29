"""
Unit tests for A2AAgent path normalization (slug merge requirement)
"""

import pytest
from beanie import PydanticObjectId

from registry_pkgs.models.a2a_agent import A2AAgent, AgentConfig, normalize_a2a_agent_path


class TestA2AAgentPathNormalization:
    """Test that path field is automatically normalized to slug format (no slashes)"""

    def test_path_normalization_strips_leading_trailing_slashes(self, monkeypatch):
        """Test that leading and trailing slashes are removed"""
        # Mock get_pymongo_collection to avoid DB dependency
        monkeypatch.setattr(A2AAgent, "get_pymongo_collection", classmethod(lambda cls: None))

        # Use model_validate to trigger validators
        agent_data = {
            "id": PydanticObjectId(),
            "path": "/test-agent/",  # Input with slashes
            "card": {
                "name": "Test Agent",
                "description": "A test agent",
                "url": "https://example.com",
                "version": "1.0.0",
                "capabilities": {},
                "defaultInputModes": ["text/plain"],
                "defaultOutputModes": ["text/plain"],
                "skills": [],
            },
            "config": {"title": "Test", "type": "jsonrpc"},
            "author": PydanticObjectId(),
            "status": "active",
        }
        agent = A2AAgent.model_validate(agent_data)

        # Path should be normalized
        assert agent.path == "test-agent"
        assert "/" not in agent.path

    def test_path_normalization_replaces_internal_slashes_with_hyphens(self, monkeypatch):
        """Test that internal slashes are replaced with hyphens"""
        # Mock get_pymongo_collection to avoid DB dependency
        monkeypatch.setattr(A2AAgent, "get_pymongo_collection", classmethod(lambda cls: None))

        agent_data = {
            "id": PydanticObjectId(),
            "path": "/agentcore/a2a/deep-intel",  # Input with multiple slashes
            "card": {
                "name": "Deep Intel",
                "description": "A deep intelligence agent",
                "url": "https://example.com",
                "version": "1.0.0",
                "capabilities": {},
                "defaultInputModes": ["text/plain"],
                "defaultOutputModes": ["text/plain"],
                "skills": [],
            },
            "config": {"title": "Deep Intel", "type": "jsonrpc"},
            "author": PydanticObjectId(),
            "status": "active",
        }
        agent = A2AAgent.model_validate(agent_data)

        # Path should be normalized: /agentcore/a2a/deep-intel -> agentcore-a2a-deep-intel
        assert agent.path == "agentcore-a2a-deep-intel"
        assert "/" not in agent.path

    def test_path_already_normalized_remains_unchanged(self, monkeypatch):
        """Test that already normalized paths are not modified"""
        # Mock get_pymongo_collection to avoid DB dependency
        monkeypatch.setattr(A2AAgent, "get_pymongo_collection", classmethod(lambda cls: None))

        agent_data = {
            "id": PydanticObjectId(),
            "path": "already-normalized",  # Already in slug format
            "card": {
                "name": "Test Agent",
                "description": "A test agent",
                "url": "https://example.com",
                "version": "1.0.0",
                "capabilities": {},
                "defaultInputModes": ["text/plain"],
                "defaultOutputModes": ["text/plain"],
                "skills": [],
            },
            "config": {"title": "Test", "type": "jsonrpc"},
            "author": PydanticObjectId(),
            "status": "active",
        }
        agent = A2AAgent.model_validate(agent_data)

        assert agent.path == "already-normalized"
        assert "/" not in agent.path

    def test_path_normalization_handles_complex_paths(self, monkeypatch):
        """Test normalization of complex federation paths"""
        # Mock get_pymongo_collection to avoid DB dependency
        monkeypatch.setattr(A2AAgent, "get_pymongo_collection", classmethod(lambda cls: None))

        test_cases = [
            ("/azure/ai-foundry/agent-1", "azure-ai-foundry-agent-1"),
            ("/aws/agentcore/us-east-1/my-agent", "aws-agentcore-us-east-1-my-agent"),
            ("///multiple///slashes///", "multiple-slashes"),
            ("/single", "single"),
            ("My Fancy Agent!", "my-fancy-agent"),
            ("team_a/CRM Agent v2", "team-a-crm-agent-v2"),
            (12345, "12345"),
        ]

        for input_path, expected_path in test_cases:
            agent_data = {
                "id": PydanticObjectId(),
                "path": input_path,
                "card": {
                    "name": "Test",
                    "description": "A test agent",
                    "url": "https://example.com",
                    "version": "1.0.0",
                    "capabilities": {},
                    "defaultInputModes": ["text/plain"],
                    "defaultOutputModes": ["text/plain"],
                    "skills": [],
                },
                "config": {"title": "Test", "type": "jsonrpc"},
                "author": PydanticObjectId(),
                "status": "active",
            }
            agent = A2AAgent.model_validate(agent_data)

            assert agent.path == expected_path, f"Input: {input_path}, Expected: {expected_path}, Got: {agent.path}"
            assert "/" not in agent.path

    @pytest.mark.parametrize(
        ("input_path", "expected_path"),
        [
            ("/path/", "path"),
            ("///path///", "path"),
            ("/a/b/c", "a-b-c"),
            ("/a///b", "a-b"),
            ("deep-intel", "deep-intel"),
            ("MyAgent", "myagent"),
            ("my_agent", "my-agent"),
            ("my.agent", "my-agent"),
            ("/Team A/CRM Agent/", "team-a-crm-agent"),
            ("café", "caf"),
        ],
    )
    def test_normalize_a2a_agent_path_returns_slug(self, input_path, expected_path):
        """Test direct path normalization for documented and migration-critical cases."""
        assert normalize_a2a_agent_path(input_path) == expected_path

    @pytest.mark.parametrize("input_path", [None, "/", "", "   ", "___", "///---___"])
    def test_normalize_a2a_agent_path_rejects_empty_slug(self, input_path):
        """Test that values without letters or numbers are rejected."""
        with pytest.raises(ValueError, match="must contain at least one letter or number|path is required"):
            normalize_a2a_agent_path(input_path)

    def test_model_validation_rejects_root_path(self, monkeypatch):
        """Test that model validation rejects root-like paths."""
        monkeypatch.setattr(A2AAgent, "get_pymongo_collection", classmethod(lambda cls: None))

        agent_data = {
            "id": PydanticObjectId(),
            "path": "/",
            "card": {
                "name": "Test",
                "description": "A test agent",
                "url": "https://example.com",
                "version": "1.0.0",
                "capabilities": {},
                "defaultInputModes": ["text/plain"],
                "defaultOutputModes": ["text/plain"],
                "skills": [],
            },
            "config": {"title": "Test", "type": "jsonrpc"},
            "author": PydanticObjectId(),
            "status": "active",
        }

        with pytest.raises(ValueError, match="cannot be '/'"):
            A2AAgent.model_validate(agent_data)

    def test_from_a2a_agent_card_normalizes_path(self, monkeypatch):
        """Test that path normalization works with from_a2a_agent_card factory method"""
        # Mock get_pymongo_collection to avoid DB dependency
        monkeypatch.setattr(A2AAgent, "get_pymongo_collection", classmethod(lambda cls: None))

        agent = A2AAgent.from_a2a_agent_card(
            card_data={
                "name": "Federation Agent",
                "description": "A federated agent",
                "url": "https://example.com/agent",
                "version": "1.0.0",
                "capabilities": {"streaming": True},
                "defaultInputModes": ["text/plain"],
                "defaultOutputModes": ["application/json"],
                "skills": [],
            },
            path="/federation/provider/agent-name",  # Path with slashes
            author=PydanticObjectId(),
            config=AgentConfig(
                title="Federation Agent",
                description="A federated agent",
                type="http_json",
            ),
        )

        # Path should be normalized
        assert agent.path == "federation-provider-agent-name"
        assert "/" not in agent.path

    def test_no_slug_field_exists(self, monkeypatch):
        """Test that the slug field has been removed from the model"""
        # Mock get_pymongo_collection to avoid DB dependency
        monkeypatch.setattr(A2AAgent, "get_pymongo_collection", classmethod(lambda cls: None))

        agent_data = {
            "id": PydanticObjectId(),
            "path": "test-agent",
            "card": {
                "name": "Test",
                "description": "A test agent",
                "url": "https://example.com",
                "version": "1.0.0",
                "capabilities": {},
                "defaultInputModes": ["text/plain"],
                "defaultOutputModes": ["text/plain"],
                "skills": [],
            },
            "config": {"title": "Test", "type": "jsonrpc"},
            "author": PydanticObjectId(),
            "status": "active",
        }
        agent = A2AAgent.model_validate(agent_data)

        # The slug field should not exist
        assert not hasattr(agent, "slug")

        # model_dump should not include 'slug'
        agent_dict = agent.model_dump()
        assert "slug" not in agent_dict
