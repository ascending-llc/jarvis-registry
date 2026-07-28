"""Tests for federation sync planning and conflict classification."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from registry.services.federation_sync_service import (
    FederationSyncService,
    _ConflictOutcome,
)
from registry_pkgs.models.enums import (
    FederationProviderType,
)
from registry_pkgs.models.federation import (
    AgentCoreRuntimeAccessConfig,
    AgentCoreRuntimeJwtConfig,
)
from registry_pkgs.models.federation_sync_job import FederationApplySummary
from tests.unit.services.federation_sync_test_helpers import (
    _FakeQuery,
    _make_federation,
)

pytestmark = pytest.mark.usefixtures("default_empty_access_roles")


@pytest.mark.asyncio
async def test_build_sync_plan_handles_runtime_type_switch_without_discovery_mutation(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    runtime_arn = "arn:aws:bedrock-agentcore:us-east-1:123:runtime/r1"

    existing_mcp = SimpleNamespace(
        federationMetadata={"runtimeArn": runtime_arn, "runtimeVersion": "1"},
        serverName="runtime-r1",
        path="/agentcore/mcp/runtime-r1",
        config={"runtimeAccess": {"mode": "iam"}},
        numTools=1,
        tags=[],
    )
    discovered_a2a = SimpleNamespace(
        federationMetadata={"runtimeArn": runtime_arn, "runtimeVersion": "2"},
        path="/agentcore/a2a/runtime-r1",
        config=SimpleNamespace(runtimeAccess=SimpleNamespace(mode="jwt")),
        card=SimpleNamespace(name="runtime-r1"),
    )

    def _fake_mcp_find(query, session=None):
        if "federationRefId" in query:
            return _FakeQuery([existing_mcp])
        if "serverName" in query:
            return _FakeQuery([])
        raise AssertionError(f"Unexpected MCP query: {query}")

    def _fake_a2a_find(query, session=None):
        if "federationRefId" in query:
            return _FakeQuery([])
        if "path" in query:
            return _FakeQuery([])
        raise AssertionError(f"Unexpected A2A query: {query}")

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedMCPServer.find", _fake_mcp_find)
    monkeypatch.setattr("registry.services.federation_sync_service.A2AAgent.find", _fake_a2a_find)

    sync_plan = await federation_sync_service._build_sync_plan(
        federation=federation,
        discovered_mcp=[],
        discovered_a2a=[discovered_a2a],
    )

    assert sync_plan.summary.createdAgents == 1
    assert sync_plan.summary.deletedMcpServers == 1
    assert sync_plan.summary.deletedAgents == 0
    assert len(sync_plan.a2a_creates) == 1
    assert len(sync_plan.mcp_deletes) == 1
    assert sync_plan.mcp_deletes[0][1] == runtime_arn


@pytest.mark.asyncio
async def test_build_sync_plan_updates_mcp_when_only_runtime_access_mode_changes(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    runtime_arn = "arn:aws:bedrock-agentcore:us-east-1:123:runtime/auth-mode-mcp"
    existing_mcp = SimpleNamespace(
        id=PydanticObjectId(),
        federationRefId=federation.id,
        federationMetadata={"runtimeArn": runtime_arn, "runtimeVersion": "1"},
        serverName="auth-mode-mcp",
        config={"runtimeAccess": {"mode": "iam"}},
    )
    discovered_mcp = SimpleNamespace(
        federationMetadata={"runtimeArn": runtime_arn, "runtimeVersion": "1"},
        serverName="auth-mode-mcp",
        config={"runtimeAccess": {"mode": "jwt"}},
    )

    def _fake_mcp_find(query, session=None):
        if "federationRefId" in query:
            return _FakeQuery([existing_mcp])
        if "serverName" in query:
            return _FakeQuery([])
        raise AssertionError(f"Unexpected MCP query: {query}")

    def _fake_a2a_find(query, session=None):
        if "federationRefId" in query:
            return _FakeQuery([])
        raise AssertionError(f"Unexpected A2A query: {query}")

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedMCPServer.find", _fake_mcp_find)
    monkeypatch.setattr("registry.services.federation_sync_service.A2AAgent.find", _fake_a2a_find)

    sync_plan = await federation_sync_service._build_sync_plan(
        federation=federation,
        discovered_mcp=[discovered_mcp],
        discovered_a2a=[],
    )

    assert sync_plan.summary.updatedMcpServers == 1
    assert sync_plan.summary.unchangedMcpServers == 0
    assert sync_plan.mcp_updates == [(existing_mcp, discovered_mcp, runtime_arn)]


@pytest.mark.asyncio
async def test_build_sync_plan_updates_a2a_when_only_runtime_access_mode_changes(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    runtime_arn = "arn:aws:bedrock-agentcore:us-east-1:123:runtime/auth-mode-a2a"
    agent_path = "/agentcore/a2a/auth-mode-a2a"
    existing_a2a = SimpleNamespace(
        id=PydanticObjectId(),
        federationRefId=federation.id,
        federationMetadata={"runtimeArn": runtime_arn, "runtimeVersion": "1"},
        path=agent_path,
        config=SimpleNamespace(type="jsonrpc", runtimeAccess=AgentCoreRuntimeAccessConfig(mode="iam")),
    )
    discovered_a2a = SimpleNamespace(
        federationMetadata={"runtimeArn": runtime_arn, "runtimeVersion": "1"},
        path=agent_path,
        card=SimpleNamespace(name="auth-mode-a2a"),
        config=SimpleNamespace(type="jsonrpc", runtimeAccess=AgentCoreRuntimeAccessConfig(mode="jwt")),
    )

    def _fake_mcp_find(query, session=None):
        if query == {"federationRefId": federation.id}:
            return _FakeQuery([])
        raise AssertionError(f"Unexpected MCP query: {query}")

    def _fake_a2a_find(query, session=None):
        if query == {"federationRefId": federation.id}:
            return _FakeQuery([existing_a2a])
        if query == {"path": {"$in": [agent_path]}}:
            return _FakeQuery([existing_a2a])
        raise AssertionError(f"Unexpected A2A query: {query}")

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedMCPServer.find", _fake_mcp_find)
    monkeypatch.setattr("registry.services.federation_sync_service.A2AAgent.find", _fake_a2a_find)

    sync_plan = await federation_sync_service._build_sync_plan(
        federation=federation,
        discovered_mcp=[],
        discovered_a2a=[discovered_a2a],
    )

    assert sync_plan.summary.updatedAgents == 1
    assert sync_plan.summary.unchangedAgents == 0
    assert sync_plan.a2a_updates == [(existing_a2a, discovered_a2a, runtime_arn)]


@pytest.mark.asyncio
async def test_build_sync_plan_ignores_a2a_transport_type_only_change(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    runtime_arn = "arn:aws:bedrock-agentcore:us-east-1:123:runtime/transport-only-a2a"
    agent_path = "/agentcore/a2a/transport-only-a2a"
    existing_a2a = SimpleNamespace(
        id=PydanticObjectId(),
        federationRefId=federation.id,
        federationMetadata={"runtimeArn": runtime_arn, "runtimeVersion": "1"},
        path=agent_path,
        config=SimpleNamespace(type="http_json", runtimeAccess=AgentCoreRuntimeAccessConfig(mode="jwt")),
    )
    discovered_a2a = SimpleNamespace(
        federationMetadata={"runtimeArn": runtime_arn, "runtimeVersion": "1"},
        path=agent_path,
        card=SimpleNamespace(name="transport-only-a2a"),
        config=SimpleNamespace(type="jsonrpc", runtimeAccess=AgentCoreRuntimeAccessConfig(mode="jwt")),
    )

    def _fake_mcp_find(query, session=None):
        if query == {"federationRefId": federation.id}:
            return _FakeQuery([])
        raise AssertionError(f"Unexpected MCP query: {query}")

    def _fake_a2a_find(query, session=None):
        if query == {"federationRefId": federation.id}:
            return _FakeQuery([existing_a2a])
        if query == {"path": {"$in": [agent_path]}}:
            return _FakeQuery([existing_a2a])
        raise AssertionError(f"Unexpected A2A query: {query}")

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedMCPServer.find", _fake_mcp_find)
    monkeypatch.setattr("registry.services.federation_sync_service.A2AAgent.find", _fake_a2a_find)

    sync_plan = await federation_sync_service._build_sync_plan(
        federation=federation,
        discovered_mcp=[],
        discovered_a2a=[discovered_a2a],
    )

    assert sync_plan.summary.updatedAgents == 0
    assert sync_plan.summary.unchangedAgents == 1
    assert sync_plan.a2a_updates == []
    assert sync_plan.a2a_pre_existing_acl_targets == [existing_a2a.id]


@pytest.mark.asyncio
async def test_build_sync_plan_updates_mcp_when_only_jwt_audiences_change(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    """Regression test: AWS rotating a JWT authorizer's allowedAudience (with mode and
    runtimeVersion unchanged) must still be classified as an update, not silently dropped."""
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    runtime_arn = "arn:aws:bedrock-agentcore:us-east-1:123:runtime/audience-rotation-mcp"
    existing_mcp = SimpleNamespace(
        id=PydanticObjectId(),
        federationRefId=federation.id,
        federationMetadata={"runtimeArn": runtime_arn, "runtimeVersion": "1"},
        serverName="audience-rotation-mcp",
        config={"runtimeAccess": {"mode": "jwt", "jwt": {"audiences": ["jarvis-services"]}}},
    )
    discovered_mcp = SimpleNamespace(
        federationMetadata={"runtimeArn": runtime_arn, "runtimeVersion": "1"},
        serverName="audience-rotation-mcp",
        config={"runtimeAccess": {"mode": "jwt", "jwt": {"audiences": ["jarvis-managed-agents"]}}},
    )

    def _fake_mcp_find(query, session=None):
        if "federationRefId" in query:
            return _FakeQuery([existing_mcp])
        if "serverName" in query:
            return _FakeQuery([])
        raise AssertionError(f"Unexpected MCP query: {query}")

    def _fake_a2a_find(query, session=None):
        if "federationRefId" in query:
            return _FakeQuery([])
        raise AssertionError(f"Unexpected A2A query: {query}")

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedMCPServer.find", _fake_mcp_find)
    monkeypatch.setattr("registry.services.federation_sync_service.A2AAgent.find", _fake_a2a_find)

    sync_plan = await federation_sync_service._build_sync_plan(
        federation=federation,
        discovered_mcp=[discovered_mcp],
        discovered_a2a=[],
    )

    assert sync_plan.summary.updatedMcpServers == 1
    assert sync_plan.summary.unchangedMcpServers == 0
    assert sync_plan.mcp_updates == [(existing_mcp, discovered_mcp, runtime_arn)]


@pytest.mark.asyncio
async def test_build_sync_plan_updates_a2a_when_only_jwt_audiences_change(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    """Regression test: same audience-rotation scenario as the MCP case above, for A2A agents."""
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    runtime_arn = "arn:aws:bedrock-agentcore:us-east-1:123:runtime/audience-rotation-a2a"
    agent_path = "/agentcore/a2a/audience-rotation-a2a"
    existing_a2a = SimpleNamespace(
        id=PydanticObjectId(),
        federationRefId=federation.id,
        federationMetadata={"runtimeArn": runtime_arn, "runtimeVersion": "1"},
        path=agent_path,
        config=SimpleNamespace(
            type="jsonrpc",
            runtimeAccess=AgentCoreRuntimeAccessConfig(
                mode="jwt", jwt=AgentCoreRuntimeJwtConfig(audiences=["jarvis-services"])
            ),
        ),
    )
    discovered_a2a = SimpleNamespace(
        federationMetadata={"runtimeArn": runtime_arn, "runtimeVersion": "1"},
        path=agent_path,
        card=SimpleNamespace(name="audience-rotation-a2a"),
        config=SimpleNamespace(
            type="jsonrpc",
            runtimeAccess=AgentCoreRuntimeAccessConfig(
                mode="jwt", jwt=AgentCoreRuntimeJwtConfig(audiences=["jarvis-managed-agents"])
            ),
        ),
    )

    def _fake_mcp_find(query, session=None):
        if query == {"federationRefId": federation.id}:
            return _FakeQuery([])
        raise AssertionError(f"Unexpected MCP query: {query}")

    def _fake_a2a_find(query, session=None):
        if query == {"federationRefId": federation.id}:
            return _FakeQuery([existing_a2a])
        if query == {"path": {"$in": [agent_path]}}:
            return _FakeQuery([existing_a2a])
        raise AssertionError(f"Unexpected A2A query: {query}")

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedMCPServer.find", _fake_mcp_find)
    monkeypatch.setattr("registry.services.federation_sync_service.A2AAgent.find", _fake_a2a_find)

    sync_plan = await federation_sync_service._build_sync_plan(
        federation=federation,
        discovered_mcp=[],
        discovered_a2a=[discovered_a2a],
    )

    assert sync_plan.summary.updatedAgents == 1
    assert sync_plan.summary.unchangedAgents == 0
    assert sync_plan.a2a_updates == [(existing_a2a, discovered_a2a, runtime_arn)]


@pytest.mark.asyncio
async def test_build_sync_plan_treats_unparseable_existing_runtime_access_as_changed(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    """Fail-open regression test: a stored runtimeAccess dict that no longer matches
    AgentCoreRuntimeAccessConfig's schema must force an update (refresh from AWS), not
    crash the sync or get silently treated as unchanged."""
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    runtime_arn = "arn:aws:bedrock-agentcore:us-east-1:123:runtime/malformed-runtime-access-mcp"
    existing_mcp = SimpleNamespace(
        id=PydanticObjectId(),
        federationRefId=federation.id,
        federationMetadata={"runtimeArn": runtime_arn, "runtimeVersion": "1"},
        serverName="malformed-runtime-access-mcp",
        config={"runtimeAccess": {"mode": "jwt", "jwt": "not-a-mapping"}},
    )
    discovered_mcp = SimpleNamespace(
        federationMetadata={"runtimeArn": runtime_arn, "runtimeVersion": "1"},
        serverName="malformed-runtime-access-mcp",
        config={"runtimeAccess": {"mode": "jwt", "jwt": {"audiences": ["jarvis-services"]}}},
    )

    def _fake_mcp_find(query, session=None):
        if "federationRefId" in query:
            return _FakeQuery([existing_mcp])
        if "serverName" in query:
            return _FakeQuery([])
        raise AssertionError(f"Unexpected MCP query: {query}")

    def _fake_a2a_find(query, session=None):
        if "federationRefId" in query:
            return _FakeQuery([])
        raise AssertionError(f"Unexpected A2A query: {query}")

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedMCPServer.find", _fake_mcp_find)
    monkeypatch.setattr("registry.services.federation_sync_service.A2AAgent.find", _fake_a2a_find)

    sync_plan = await federation_sync_service._build_sync_plan(
        federation=federation,
        discovered_mcp=[discovered_mcp],
        discovered_a2a=[],
    )

    assert sync_plan.summary.updatedMcpServers == 1
    assert sync_plan.summary.unchangedMcpServers == 0
    assert sync_plan.mcp_updates == [(existing_mcp, discovered_mcp, runtime_arn)]


@pytest.mark.asyncio
async def test_build_sync_plan_skips_a2a_insert_when_path_belongs_to_another_resource(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    conflicting_agent = SimpleNamespace(
        id=PydanticObjectId(),
        path="/agentcore/a2a/hosted-agent-257ko",
        federationRefId=PydanticObjectId(),
        federationMetadata={"runtimeArn": "arn:existing"},
    )
    discovered_agent = SimpleNamespace(
        id=PydanticObjectId(),
        path="/agentcore/a2a/hosted-agent-257ko",
        card=SimpleNamespace(name="hosted_agent_257ko"),
        config=SimpleNamespace(enabled=True),
        tags=[],
        wellKnown=None,
        federationRefId=None,
        federationMetadata={"runtimeArn": "arn:new", "runtimeVersion": "1"},
        insert=AsyncMock(),
    )

    def _fake_mcp_find(*_args, **_kwargs):
        return _FakeQuery([])

    def _fake_a2a_find(query, session=None):
        if "federationRefId" in query:
            return _FakeQuery([])
        if "path" in query:
            return _FakeQuery([conflicting_agent])
        raise AssertionError(f"unexpected query: {query}")

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedMCPServer.find", _fake_mcp_find)
    monkeypatch.setattr("registry.services.federation_sync_service.A2AAgent.find", _fake_a2a_find)

    result = await federation_sync_service._build_sync_plan(
        federation=federation,
        discovered_mcp=[],
        discovered_a2a=[discovered_agent],
    )

    assert result.summary.skippedAgents == 1
    assert result.summary.createdAgents == 0
    assert result.summary.errors == 0
    assert result.summary.errorMessages == []
    discovered_agent.insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_sync_plan_skips_mcp_insert_without_marking_error(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    conflicting_server = SimpleNamespace(
        id=PydanticObjectId(),
        serverName="shared-server",
        federationRefId=PydanticObjectId(),
        federationMetadata={"runtimeArn": "arn:existing"},
    )
    discovered_server = SimpleNamespace(
        id=PydanticObjectId(),
        serverName="shared-server",
        tags=[],
        federationRefId=None,
        federationMetadata={"runtimeArn": "arn:new", "runtimeVersion": "1"},
        insert=AsyncMock(),
    )

    def _fake_mcp_find(query, session=None):
        if "federationRefId" in query:
            return _FakeQuery([])
        if "serverName" in query:
            return _FakeQuery([conflicting_server])
        raise AssertionError(f"unexpected query: {query}")

    def _fake_a2a_find(*_args, **_kwargs):
        return _FakeQuery([])

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedMCPServer.find", _fake_mcp_find)
    monkeypatch.setattr("registry.services.federation_sync_service.A2AAgent.find", _fake_a2a_find)

    result = await federation_sync_service._build_sync_plan(
        federation=federation,
        discovered_mcp=[discovered_server],
        discovered_a2a=[],
    )

    assert result.summary.skippedMcpServers == 1
    assert result.summary.createdMcpServers == 0
    assert result.summary.errors == 0
    assert result.summary.errorMessages == []
    discovered_server.insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_sync_plan_does_not_treat_planned_a2a_create_as_persisted_path_owner(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    existing_agent = SimpleNamespace(
        id=PydanticObjectId(),
        path="/agentcore/a2a/existing-path",
        card=SimpleNamespace(name="existing"),
        config=SimpleNamespace(enabled=True),
        tags=[],
        wellKnown=None,
        federationRefId=federation.id,
        federationMetadata={"runtimeArn": "arn:existing", "runtimeVersion": "1"},
    )
    discovered_new_agent = SimpleNamespace(
        id=None,
        path="/agentcore/a2a/target-path",
        card=SimpleNamespace(name="new-agent"),
        config=SimpleNamespace(enabled=True),
        tags=[],
        wellKnown=None,
        federationRefId=None,
        federationMetadata={"runtimeArn": "arn:new", "runtimeVersion": "1"},
        insert=AsyncMock(),
    )
    discovered_existing_agent = SimpleNamespace(
        id=PydanticObjectId(),
        path="/agentcore/a2a/target-path",
        card=SimpleNamespace(name="existing"),
        config=SimpleNamespace(enabled=True),
        tags=[],
        wellKnown=None,
        federationRefId=federation.id,
        federationMetadata={"runtimeArn": "arn:existing", "runtimeVersion": "2"},
        insert=AsyncMock(),
    )

    def _fake_mcp_find(*_args, **_kwargs):
        return _FakeQuery([])

    def _fake_a2a_find(query, session=None):
        if "federationRefId" in query:
            return _FakeQuery([existing_agent])
        if "path" in query:
            return _FakeQuery([])
        raise AssertionError(f"unexpected query: {query}")

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedMCPServer.find", _fake_mcp_find)
    monkeypatch.setattr("registry.services.federation_sync_service.A2AAgent.find", _fake_a2a_find)

    result = await federation_sync_service._build_sync_plan(
        federation=federation,
        discovered_mcp=[],
        discovered_a2a=[discovered_new_agent, discovered_existing_agent],
    )

    assert result.summary.createdAgents == 1
    assert result.summary.updatedAgents == 0
    assert result.summary.skippedAgents == 1
    assert result.summary.errors == 1
    assert any("collides with another resource discovered in this same sync" in m for m in result.summary.errorMessages)
    assert result.a2a_creates == [(discovered_new_agent, "arn:new")]
    assert result.a2a_updates == []


@pytest.mark.asyncio
async def test_build_sync_plan_records_error_when_a2a_create_path_has_no_federation_owner(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    orphaned_agent = SimpleNamespace(
        id=PydanticObjectId(),
        path="/agentcore/a2a/orphaned-path",
        federationRefId=None,
        federationMetadata={"runtimeArn": "arn:orphaned"},
    )
    discovered_agent = SimpleNamespace(
        id=PydanticObjectId(),
        path="/agentcore/a2a/orphaned-path",
        card=SimpleNamespace(name="new_agent"),
        config=SimpleNamespace(enabled=True),
        tags=[],
        wellKnown=None,
        federationRefId=None,
        federationMetadata={"runtimeArn": "arn:new", "runtimeVersion": "1"},
        insert=AsyncMock(),
    )

    def _fake_mcp_find(*_args, **_kwargs):
        return _FakeQuery([])

    def _fake_a2a_find(query, session=None):
        if "federationRefId" in query:
            return _FakeQuery([])
        if "path" in query:
            return _FakeQuery([orphaned_agent])
        raise AssertionError(f"unexpected query: {query}")

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedMCPServer.find", _fake_mcp_find)
    monkeypatch.setattr("registry.services.federation_sync_service.A2AAgent.find", _fake_a2a_find)

    result = await federation_sync_service._build_sync_plan(
        federation=federation,
        discovered_mcp=[],
        discovered_a2a=[discovered_agent],
    )

    assert result.summary.skippedAgents == 1
    assert result.summary.createdAgents == 0
    assert result.summary.errors == 1
    assert any("not owned by any federation" in m for m in result.summary.errorMessages)


@pytest.mark.asyncio
async def test_build_sync_plan_records_error_when_a2a_rename_path_has_no_federation_owner(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    existing_agent = SimpleNamespace(
        id=PydanticObjectId(),
        path="/agentcore/a2a/old-path",
        card=SimpleNamespace(name="agent"),
        config=SimpleNamespace(enabled=True),
        tags=[],
        wellKnown=None,
        federationRefId=federation.id,
        federationMetadata={"runtimeArn": "arn:existing", "runtimeVersion": "1"},
    )
    orphaned_agent = SimpleNamespace(
        id=PydanticObjectId(),
        path="/agentcore/a2a/target-path",
        federationRefId=None,
        federationMetadata={"runtimeArn": "arn:orphaned"},
    )
    discovered_agent = SimpleNamespace(
        id=PydanticObjectId(),
        path="/agentcore/a2a/target-path",
        card=SimpleNamespace(name="agent"),
        config=SimpleNamespace(enabled=True),
        tags=[],
        wellKnown=None,
        federationRefId=federation.id,
        federationMetadata={"runtimeArn": "arn:existing", "runtimeVersion": "2"},
    )

    def _fake_mcp_find(*_args, **_kwargs):
        return _FakeQuery([])

    def _fake_a2a_find(query, session=None):
        if "federationRefId" in query:
            return _FakeQuery([existing_agent])
        if "path" in query:
            return _FakeQuery([orphaned_agent])
        raise AssertionError(f"unexpected query: {query}")

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedMCPServer.find", _fake_mcp_find)
    monkeypatch.setattr("registry.services.federation_sync_service.A2AAgent.find", _fake_a2a_find)

    result = await federation_sync_service._build_sync_plan(
        federation=federation,
        discovered_mcp=[],
        discovered_a2a=[discovered_agent],
    )

    assert result.summary.skippedAgents == 1
    assert result.summary.updatedAgents == 0
    assert result.summary.errors == 1
    assert any("not owned by any federation" in m for m in result.summary.errorMessages)


@pytest.mark.asyncio
async def test_build_sync_plan_records_error_when_mcp_create_servername_has_no_federation_owner(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    orphaned_server = SimpleNamespace(
        id=PydanticObjectId(),
        serverName="orphaned-server",
        federationRefId=None,
        federationMetadata={"runtimeArn": "arn:orphaned"},
    )
    discovered_server = SimpleNamespace(
        id=PydanticObjectId(),
        serverName="orphaned-server",
        tags=[],
        federationRefId=None,
        federationMetadata={"runtimeArn": "arn:new", "runtimeVersion": "1"},
        insert=AsyncMock(),
    )

    def _fake_mcp_find(query, session=None):
        if "federationRefId" in query:
            return _FakeQuery([])
        if "serverName" in query:
            return _FakeQuery([orphaned_server])
        raise AssertionError(f"unexpected query: {query}")

    def _fake_a2a_find(*_args, **_kwargs):
        return _FakeQuery([])

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedMCPServer.find", _fake_mcp_find)
    monkeypatch.setattr("registry.services.federation_sync_service.A2AAgent.find", _fake_a2a_find)

    result = await federation_sync_service._build_sync_plan(
        federation=federation,
        discovered_mcp=[discovered_server],
        discovered_a2a=[],
    )

    assert result.summary.skippedMcpServers == 1
    assert result.summary.createdMcpServers == 0
    assert result.summary.errors == 1
    assert any("not owned by any federation" in m for m in result.summary.errorMessages)


@pytest.mark.asyncio
async def test_build_sync_plan_skips_mcp_rename_when_servername_owned_by_another_federation(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    existing_server = SimpleNamespace(
        id=PydanticObjectId(),
        serverName="old-name",
        path="/agentcore/mcp/old-name",
        config={"runtimeAccess": {"mode": "public"}},
        tags=[],
        federationRefId=federation.id,
        federationMetadata={"runtimeArn": "arn:existing", "runtimeVersion": "1"},
    )
    conflict_server = SimpleNamespace(
        id=PydanticObjectId(),
        serverName="taken-name",
        federationRefId=PydanticObjectId(),
        federationMetadata={"runtimeArn": "arn:other"},
    )
    discovered_server = SimpleNamespace(
        id=PydanticObjectId(),
        serverName="taken-name",
        path="/agentcore/mcp/taken-name",
        config={"runtimeAccess": {"mode": "public"}},
        tags=[],
        federationRefId=None,
        federationMetadata={"runtimeArn": "arn:existing", "runtimeVersion": "2"},
    )

    def _fake_mcp_find(query, session=None):
        if "federationRefId" in query:
            return _FakeQuery([existing_server])
        if "serverName" in query:
            return _FakeQuery([conflict_server])
        raise AssertionError(f"unexpected query: {query}")

    def _fake_a2a_find(*_args, **_kwargs):
        return _FakeQuery([])

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedMCPServer.find", _fake_mcp_find)
    monkeypatch.setattr("registry.services.federation_sync_service.A2AAgent.find", _fake_a2a_find)

    result = await federation_sync_service._build_sync_plan(
        federation=federation,
        discovered_mcp=[discovered_server],
        discovered_a2a=[],
    )

    assert result.summary.skippedMcpServers == 1
    assert result.summary.updatedMcpServers == 0
    assert result.summary.errors == 0
    assert result.summary.errorMessages == []


@pytest.mark.asyncio
async def test_build_sync_plan_records_error_when_mcp_rename_servername_has_no_federation_owner(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    existing_server = SimpleNamespace(
        id=PydanticObjectId(),
        serverName="old-name",
        path="/agentcore/mcp/old-name",
        config={"runtimeAccess": {"mode": "public"}},
        tags=[],
        federationRefId=federation.id,
        federationMetadata={"runtimeArn": "arn:existing", "runtimeVersion": "1"},
    )
    orphaned_server = SimpleNamespace(
        id=PydanticObjectId(),
        serverName="orphaned-name",
        federationRefId=None,
        federationMetadata={"runtimeArn": "arn:orphaned"},
    )
    discovered_server = SimpleNamespace(
        id=PydanticObjectId(),
        serverName="orphaned-name",
        path="/agentcore/mcp/orphaned-name",
        config={"runtimeAccess": {"mode": "public"}},
        tags=[],
        federationRefId=None,
        federationMetadata={"runtimeArn": "arn:existing", "runtimeVersion": "2"},
    )

    def _fake_mcp_find(query, session=None):
        if "federationRefId" in query:
            return _FakeQuery([existing_server])
        if "serverName" in query:
            return _FakeQuery([orphaned_server])
        raise AssertionError(f"unexpected query: {query}")

    def _fake_a2a_find(*_args, **_kwargs):
        return _FakeQuery([])

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedMCPServer.find", _fake_mcp_find)
    monkeypatch.setattr("registry.services.federation_sync_service.A2AAgent.find", _fake_a2a_find)

    result = await federation_sync_service._build_sync_plan(
        federation=federation,
        discovered_mcp=[discovered_server],
        discovered_a2a=[],
    )

    assert result.summary.skippedMcpServers == 1
    assert result.summary.updatedMcpServers == 0
    assert result.summary.errors == 1
    assert any("not owned by any federation" in m for m in result.summary.errorMessages)


@pytest.mark.asyncio
async def test_build_sync_plan_same_batch_a2a_create_collision(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    agent_a = SimpleNamespace(
        id=None,
        path="/agentcore/a2a/colliding-path",
        card=SimpleNamespace(name="agent-a"),
        config=SimpleNamespace(enabled=True),
        tags=[],
        wellKnown=None,
        federationRefId=None,
        federationMetadata={"runtimeArn": "arn:a", "runtimeVersion": "1"},
        insert=AsyncMock(),
    )
    agent_b = SimpleNamespace(
        id=None,
        path="/agentcore/a2a/colliding-path",
        card=SimpleNamespace(name="agent-b"),
        config=SimpleNamespace(enabled=True),
        tags=[],
        wellKnown=None,
        federationRefId=None,
        federationMetadata={"runtimeArn": "arn:b", "runtimeVersion": "1"},
        insert=AsyncMock(),
    )

    def _fake_mcp_find(*_args, **_kwargs):
        return _FakeQuery([])

    def _fake_a2a_find(query, session=None):
        if "federationRefId" in query:
            return _FakeQuery([])
        if "path" in query:
            return _FakeQuery([])
        raise AssertionError(f"unexpected query: {query}")

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedMCPServer.find", _fake_mcp_find)
    monkeypatch.setattr("registry.services.federation_sync_service.A2AAgent.find", _fake_a2a_find)

    result = await federation_sync_service._build_sync_plan(
        federation=federation,
        discovered_mcp=[],
        discovered_a2a=[agent_a, agent_b],
    )

    assert result.summary.createdAgents == 1
    assert result.summary.skippedAgents == 1
    assert result.summary.errors == 1
    assert any("collides with another resource discovered in this same sync" in m for m in result.summary.errorMessages)
    assert result.a2a_creates == [(agent_a, "arn:a")]


@pytest.mark.asyncio
async def test_build_sync_plan_same_batch_mcp_create_collision(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    server_a = SimpleNamespace(
        id=None,
        serverName="colliding-name",
        tags=[],
        federationRefId=None,
        federationMetadata={"runtimeArn": "arn:a", "runtimeVersion": "1"},
        insert=AsyncMock(),
    )
    server_b = SimpleNamespace(
        id=None,
        serverName="colliding-name",
        tags=[],
        federationRefId=None,
        federationMetadata={"runtimeArn": "arn:b", "runtimeVersion": "1"},
        insert=AsyncMock(),
    )

    def _fake_mcp_find(query, session=None):
        if "federationRefId" in query:
            return _FakeQuery([])
        if "serverName" in query:
            return _FakeQuery([])
        raise AssertionError(f"unexpected query: {query}")

    def _fake_a2a_find(*_args, **_kwargs):
        return _FakeQuery([])

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedMCPServer.find", _fake_mcp_find)
    monkeypatch.setattr("registry.services.federation_sync_service.A2AAgent.find", _fake_a2a_find)

    result = await federation_sync_service._build_sync_plan(
        federation=federation,
        discovered_mcp=[server_a, server_b],
        discovered_a2a=[],
    )

    assert result.summary.createdMcpServers == 1
    assert result.summary.skippedMcpServers == 1
    assert result.summary.errors == 1
    assert any("collides with another resource discovered in this same sync" in m for m in result.summary.errorMessages)
    assert result.mcp_creates == [(server_a, "arn:a")]


@pytest.mark.asyncio
async def test_build_sync_plan_same_batch_mcp_rename_collision(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    new_server = SimpleNamespace(
        id=None,
        serverName="target-name",
        tags=[],
        federationRefId=None,
        federationMetadata={"runtimeArn": "arn:new", "runtimeVersion": "1"},
        insert=AsyncMock(),
    )
    existing_server = SimpleNamespace(
        id=PydanticObjectId(),
        serverName="old-name",
        path="/agentcore/mcp/old-name",
        config={"runtimeAccess": {"mode": "public"}},
        tags=[],
        federationRefId=federation.id,
        federationMetadata={"runtimeArn": "arn:existing", "runtimeVersion": "1"},
    )
    discovered_existing = SimpleNamespace(
        id=PydanticObjectId(),
        serverName="target-name",
        path="/agentcore/mcp/target-name",
        config={"runtimeAccess": {"mode": "public"}},
        tags=[],
        federationRefId=None,
        federationMetadata={"runtimeArn": "arn:existing", "runtimeVersion": "2"},
    )

    def _fake_mcp_find(query, session=None):
        if "federationRefId" in query:
            return _FakeQuery([existing_server])
        if "serverName" in query:
            return _FakeQuery([])
        raise AssertionError(f"unexpected query: {query}")

    def _fake_a2a_find(*_args, **_kwargs):
        return _FakeQuery([])

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedMCPServer.find", _fake_mcp_find)
    monkeypatch.setattr("registry.services.federation_sync_service.A2AAgent.find", _fake_a2a_find)

    result = await federation_sync_service._build_sync_plan(
        federation=federation,
        discovered_mcp=[new_server, discovered_existing],
        discovered_a2a=[],
    )

    assert result.summary.createdMcpServers == 1
    assert result.summary.skippedMcpServers == 1
    assert result.summary.errors == 1
    assert any("collides with another resource discovered in this same sync" in m for m in result.summary.errorMessages)


class TestCheckPersistedConflict:
    def test_returns_no_conflict_when_doc_is_none(self):
        result = FederationSyncService._check_persisted_conflict(None, PydanticObjectId())
        assert result == _ConflictOutcome.NO_CONFLICT

    def test_returns_no_conflict_when_same_self(self):
        self_id = PydanticObjectId()
        doc = SimpleNamespace(id=self_id, federationRefId=PydanticObjectId())
        result = FederationSyncService._check_persisted_conflict(doc, PydanticObjectId(), existing_self_id=self_id)
        assert result == _ConflictOutcome.NO_CONFLICT

    def test_returns_skip_with_error_when_orphaned(self):
        doc = SimpleNamespace(id=PydanticObjectId(), federationRefId=None)
        result = FederationSyncService._check_persisted_conflict(doc, PydanticObjectId())
        assert result == _ConflictOutcome.SKIP_WITH_ERROR

    def test_returns_skip_silent_when_cross_federation(self):
        doc = SimpleNamespace(id=PydanticObjectId(), federationRefId=PydanticObjectId())
        different_federation = PydanticObjectId()
        result = FederationSyncService._check_persisted_conflict(doc, different_federation)
        assert result == _ConflictOutcome.SKIP_SILENT

    def test_returns_no_conflict_when_same_federation(self):
        fed_id = PydanticObjectId()
        doc = SimpleNamespace(id=PydanticObjectId(), federationRefId=fed_id)
        result = FederationSyncService._check_persisted_conflict(doc, fed_id)
        assert result == _ConflictOutcome.NO_CONFLICT


class TestCheckBatchConflict:
    def test_returns_true_when_key_exists(self):
        assert FederationSyncService._check_batch_conflict("my-key", {"my-key": object()}) is True

    def test_returns_false_when_key_absent(self):
        assert FederationSyncService._check_batch_conflict("my-key", {"other-key": object()}) is False

    def test_returns_false_when_dict_empty(self):
        assert FederationSyncService._check_batch_conflict("my-key", {}) is False


class TestIsResourceUnchanged:
    def test_unchanged_when_metadata_and_config_same(self, federation_sync_service):
        existing = SimpleNamespace(
            federationMetadata={"runtimeArn": "arn:1", "runtimeVersion": "1"},
            config={"runtimeAccess": {"mode": "iam"}},
        )
        discovered = SimpleNamespace(
            federationMetadata={"runtimeArn": "arn:1", "runtimeVersion": "1"},
            config={"runtimeAccess": {"mode": "iam"}},
        )
        assert federation_sync_service._is_resource_unchanged(existing, discovered) is True

    def test_changed_when_metadata_differs(self, federation_sync_service):
        existing = SimpleNamespace(
            federationMetadata={"runtimeArn": "arn:1", "runtimeVersion": "1"},
            config={"runtimeAccess": {"mode": "iam"}},
        )
        discovered = SimpleNamespace(
            federationMetadata={"runtimeArn": "arn:1", "runtimeVersion": "2"},
            config={"runtimeAccess": {"mode": "iam"}},
        )
        assert federation_sync_service._is_resource_unchanged(existing, discovered) is False

    def test_changed_when_config_differs(self, federation_sync_service):
        existing = SimpleNamespace(
            federationMetadata={"runtimeArn": "arn:1", "runtimeVersion": "1"},
            config={"runtimeAccess": {"mode": "iam"}},
        )
        discovered = SimpleNamespace(
            federationMetadata={"runtimeArn": "arn:1", "runtimeVersion": "1"},
            config={"runtimeAccess": {"mode": "jwt"}},
        )
        assert federation_sync_service._is_resource_unchanged(existing, discovered) is False


class TestCollectStaleItems:
    def test_marks_undiscovered_docs_for_deletion(self, federation_sync_service):
        doc_stale = SimpleNamespace(federationMetadata={"runtimeArn": "arn:stale"})
        doc_kept = SimpleNamespace(federationMetadata={"runtimeArn": "arn:kept"})
        summary = FederationApplySummary()
        delete_list = []

        federation_sync_service._collect_stale_items(
            [doc_stale, doc_kept], {"arn:kept"}, summary, delete_list, "deletedMcpServers"
        )

        assert summary.deletedMcpServers == 1
        assert delete_list == [(doc_stale, "arn:stale")]

    def test_keeps_all_when_all_rediscovered(self, federation_sync_service):
        doc = SimpleNamespace(federationMetadata={"runtimeArn": "arn:1"})
        summary = FederationApplySummary()
        delete_list = []

        federation_sync_service._collect_stale_items([doc], {"arn:1"}, summary, delete_list, "deletedAgents")

        assert summary.deletedAgents == 0
        assert delete_list == []

    def test_ignores_docs_without_arn(self, federation_sync_service):
        doc = SimpleNamespace(federationMetadata={})
        summary = FederationApplySummary()
        delete_list = []

        federation_sync_service._collect_stale_items([doc], set(), summary, delete_list, "deletedMcpServers")

        assert summary.deletedMcpServers == 0
        assert delete_list == []


class TestSkipOnConflict:
    def test_returns_false_when_no_conflict(self, federation_sync_service):
        summary = FederationApplySummary()
        result = federation_sync_service._skip_on_conflict(
            summary, PydanticObjectId(), "my-server", "MCP server", None, {}
        )
        assert result is False
        assert summary.errors == 0

    def test_returns_true_with_error_for_orphaned(self, federation_sync_service):
        orphaned = SimpleNamespace(id=PydanticObjectId(), federationRefId=None)
        summary = FederationApplySummary()
        result = federation_sync_service._skip_on_conflict(
            summary, PydanticObjectId(), "my-server", "MCP server", orphaned, {}
        )
        assert result is True
        assert summary.errors == 1
        assert "not owned by any federation" in summary.errorMessages[0]
        assert "'my-server'" in summary.errorMessages[0]

    def test_returns_true_silent_for_cross_federation(self, federation_sync_service):
        cross = SimpleNamespace(id=PydanticObjectId(), federationRefId=PydanticObjectId())
        summary = FederationApplySummary()
        result = federation_sync_service._skip_on_conflict(
            summary, PydanticObjectId(), "my-server", "MCP server", cross, {}
        )
        assert result is True
        assert summary.errors == 0
        assert summary.errorMessages == []

    def test_returns_true_with_error_for_batch_collision(self, federation_sync_service):
        summary = FederationApplySummary()
        planned = {"my-server": object()}
        result = federation_sync_service._skip_on_conflict(
            summary, PydanticObjectId(), "my-server", "MCP server", None, planned
        )
        assert result is True
        assert summary.errors == 1
        assert "collides with another resource discovered in this same sync" in summary.errorMessages[0]
        assert "'my-server'" in summary.errorMessages[0]

    def test_orphaned_path_label_uses_path_format(self, federation_sync_service):
        orphaned = SimpleNamespace(id=PydanticObjectId(), federationRefId=None)
        summary = FederationApplySummary()
        federation_sync_service._skip_on_conflict(
            summary,
            PydanticObjectId(),
            "/a2a/my-agent",
            "A2A agent x",
            orphaned,
            {},
            key_label="path",
        )
        assert "path '/a2a/my-agent' already exists" in summary.errorMessages[0]

    def test_batch_collision_path_label_uses_path_format(self, federation_sync_service):
        summary = FederationApplySummary()
        planned = {"/a2a/my-agent": object()}
        federation_sync_service._skip_on_conflict(
            summary,
            PydanticObjectId(),
            "/a2a/my-agent",
            "A2A agent x",
            None,
            planned,
            key_label="path",
        )
        assert "path '/a2a/my-agent' collides with another resource" in summary.errorMessages[0]

    def test_persisted_conflict_checked_before_batch(self, federation_sync_service):
        orphaned = SimpleNamespace(id=PydanticObjectId(), federationRefId=None)
        summary = FederationApplySummary()
        planned = {"my-server": object()}
        result = federation_sync_service._skip_on_conflict(
            summary, PydanticObjectId(), "my-server", "MCP server", orphaned, planned
        )
        assert result is True
        assert summary.errors == 1
        assert "not owned by any federation" in summary.errorMessages[0]
        assert "'my-server'" in summary.errorMessages[0]

    def test_self_conflict_returns_false(self, federation_sync_service):
        self_id = PydanticObjectId()
        self_doc = SimpleNamespace(id=self_id, federationRefId=PydanticObjectId())
        summary = FederationApplySummary()
        result = federation_sync_service._skip_on_conflict(
            summary,
            PydanticObjectId(),
            "my-server",
            "MCP server",
            self_doc,
            {},
            existing_self_id=self_id,
        )
        assert result is False
        assert summary.errors == 0


@pytest.mark.asyncio
async def test_build_sync_plan_skips_mcp_create_on_enrichment_error(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    discovered = SimpleNamespace(
        serverName="broken-server",
        federationMetadata={"runtimeArn": "arn:broken", "runtimeVersion": "1", "enrichmentError": "timeout"},
    )

    def _fake_mcp_find(query, session=None):
        if "federationRefId" in query:
            return _FakeQuery([])
        if "serverName" in query:
            return _FakeQuery([])
        raise AssertionError(f"Unexpected MCP query: {query}")

    def _fake_a2a_find(*_args, **_kwargs):
        return _FakeQuery([])

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedMCPServer.find", _fake_mcp_find)
    monkeypatch.setattr("registry.services.federation_sync_service.A2AAgent.find", _fake_a2a_find)

    result = await federation_sync_service._build_sync_plan(
        federation=federation, discovered_mcp=[discovered], discovered_a2a=[]
    )

    assert result.summary.createdMcpServers == 0
    assert result.summary.errors == 1
    assert "timeout" in result.summary.errorMessages[0]
    assert result.mcp_creates == []


@pytest.mark.asyncio
async def test_build_sync_plan_skips_a2a_create_on_enrichment_error(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    discovered = SimpleNamespace(
        path="/agentcore/a2a/broken",
        card=SimpleNamespace(name="broken-agent"),
        config=SimpleNamespace(enabled=True),
        federationMetadata={"runtimeArn": "arn:broken", "runtimeVersion": "1", "enrichmentError": "connection refused"},
    )

    def _fake_mcp_find(*_args, **_kwargs):
        return _FakeQuery([])

    def _fake_a2a_find(query, session=None):
        if "federationRefId" in query:
            return _FakeQuery([])
        if "path" in query:
            return _FakeQuery([])
        raise AssertionError(f"Unexpected A2A query: {query}")

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedMCPServer.find", _fake_mcp_find)
    monkeypatch.setattr("registry.services.federation_sync_service.A2AAgent.find", _fake_a2a_find)

    result = await federation_sync_service._build_sync_plan(
        federation=federation, discovered_mcp=[], discovered_a2a=[discovered]
    )

    assert result.summary.createdAgents == 0
    assert result.summary.errors == 1
    assert "connection refused" in result.summary.errorMessages[0]
    assert result.a2a_creates == []


@pytest.mark.asyncio
async def test_build_sync_plan_records_error_when_mcp_missing_remote_id(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    discovered = SimpleNamespace(
        serverName="no-arn-server",
        federationMetadata={},
    )

    def _fake_mcp_find(query, session=None):
        if "federationRefId" in query:
            return _FakeQuery([])
        if "serverName" in query:
            return _FakeQuery([])
        raise AssertionError(f"Unexpected MCP query: {query}")

    def _fake_a2a_find(*_args, **_kwargs):
        return _FakeQuery([])

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedMCPServer.find", _fake_mcp_find)
    monkeypatch.setattr("registry.services.federation_sync_service.A2AAgent.find", _fake_a2a_find)

    result = await federation_sync_service._build_sync_plan(
        federation=federation, discovered_mcp=[discovered], discovered_a2a=[]
    )

    assert result.summary.createdMcpServers == 0
    assert result.summary.errors == 1
    assert "missing a stable remote identifier" in result.summary.errorMessages[0]


@pytest.mark.asyncio
async def test_build_sync_plan_records_error_when_a2a_missing_remote_id(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    discovered = SimpleNamespace(
        path="/agentcore/a2a/no-arn",
        card=SimpleNamespace(name="no-arn-agent"),
        config=SimpleNamespace(enabled=True),
        federationMetadata={},
    )

    def _fake_mcp_find(*_args, **_kwargs):
        return _FakeQuery([])

    def _fake_a2a_find(query, session=None):
        if "federationRefId" in query:
            return _FakeQuery([])
        if "path" in query:
            return _FakeQuery([])
        raise AssertionError(f"Unexpected A2A query: {query}")

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedMCPServer.find", _fake_mcp_find)
    monkeypatch.setattr("registry.services.federation_sync_service.A2AAgent.find", _fake_a2a_find)

    result = await federation_sync_service._build_sync_plan(
        federation=federation, discovered_mcp=[], discovered_a2a=[discovered]
    )

    assert result.summary.createdAgents == 0
    assert result.summary.errors == 1
    assert "missing a stable remote identifier" in result.summary.errorMessages[0]


@pytest.mark.asyncio
async def test_build_sync_plan_same_batch_a2a_rename_collision(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    new_agent = SimpleNamespace(
        id=None,
        path="/agentcore/a2a/target-path",
        card=SimpleNamespace(name="new-agent"),
        config=SimpleNamespace(enabled=True),
        tags=[],
        wellKnown=None,
        federationRefId=None,
        federationMetadata={"runtimeArn": "arn:new", "runtimeVersion": "1"},
    )
    existing_agent = SimpleNamespace(
        id=PydanticObjectId(),
        path="/agentcore/a2a/old-path",
        card=SimpleNamespace(name="existing-agent"),
        config=SimpleNamespace(enabled=True),
        tags=[],
        wellKnown=None,
        federationRefId=federation.id,
        federationMetadata={"runtimeArn": "arn:existing", "runtimeVersion": "1"},
    )
    discovered_existing = SimpleNamespace(
        id=PydanticObjectId(),
        path="/agentcore/a2a/target-path",
        card=SimpleNamespace(name="existing-agent"),
        config=SimpleNamespace(enabled=True),
        tags=[],
        wellKnown=None,
        federationRefId=federation.id,
        federationMetadata={"runtimeArn": "arn:existing", "runtimeVersion": "2"},
    )

    def _fake_mcp_find(*_args, **_kwargs):
        return _FakeQuery([])

    def _fake_a2a_find(query, session=None):
        if "federationRefId" in query:
            return _FakeQuery([existing_agent])
        if "path" in query:
            return _FakeQuery([])
        raise AssertionError(f"Unexpected A2A query: {query}")

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedMCPServer.find", _fake_mcp_find)
    monkeypatch.setattr("registry.services.federation_sync_service.A2AAgent.find", _fake_a2a_find)

    result = await federation_sync_service._build_sync_plan(
        federation=federation, discovered_mcp=[], discovered_a2a=[new_agent, discovered_existing]
    )

    assert result.summary.createdAgents == 1
    assert result.summary.skippedAgents == 1
    assert result.summary.errors == 1
    assert any("collides with another resource discovered in this same sync" in m for m in result.summary.errorMessages)
    assert result.a2a_creates == [(new_agent, "arn:new")]


@pytest.mark.asyncio
async def test_build_sync_plan_mcp_enrichment_error_does_not_skip_stale_detection(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    """Enrichment-failed items must still be in discovered_ids so they don't get stale-deleted."""
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    runtime_arn = "arn:aws:bedrock-agentcore:us-east-1:123:runtime/enrichment-fail"
    existing = SimpleNamespace(
        id=PydanticObjectId(),
        federationRefId=federation.id,
        federationMetadata={"runtimeArn": runtime_arn, "runtimeVersion": "1"},
        serverName="enrich-fail-server",
        config={"runtimeAccess": {"mode": "iam"}},
    )
    discovered = SimpleNamespace(
        serverName="enrich-fail-server",
        federationMetadata={"runtimeArn": runtime_arn, "runtimeVersion": "2", "enrichmentError": "500 error"},
    )

    def _fake_mcp_find(query, session=None):
        if "federationRefId" in query:
            return _FakeQuery([existing])
        if "serverName" in query:
            return _FakeQuery([existing])
        raise AssertionError(f"Unexpected MCP query: {query}")

    def _fake_a2a_find(*_args, **_kwargs):
        return _FakeQuery([])

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedMCPServer.find", _fake_mcp_find)
    monkeypatch.setattr("registry.services.federation_sync_service.A2AAgent.find", _fake_a2a_find)

    result = await federation_sync_service._build_sync_plan(
        federation=federation, discovered_mcp=[discovered], discovered_a2a=[]
    )

    assert result.summary.errors == 1
    assert result.summary.deletedMcpServers == 0
    assert result.summary.updatedMcpServers == 0
    assert result.mcp_deletes == []


@pytest.mark.asyncio
async def test_build_sync_plan_mcp_update_unchanged_tracks_for_acl(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    runtime_arn = "arn:aws:bedrock-agentcore:us-east-1:123:runtime/unchanged"
    existing_id = PydanticObjectId()
    existing = SimpleNamespace(
        id=existing_id,
        federationRefId=federation.id,
        federationMetadata={"runtimeArn": runtime_arn, "runtimeVersion": "1"},
        serverName="steady-server",
        config={"runtimeAccess": {"mode": "iam"}},
    )
    discovered = SimpleNamespace(
        serverName="steady-server",
        federationMetadata={"runtimeArn": runtime_arn, "runtimeVersion": "1"},
        config={"runtimeAccess": {"mode": "iam"}},
    )

    def _fake_mcp_find(query, session=None):
        if "federationRefId" in query:
            return _FakeQuery([existing])
        if "serverName" in query:
            return _FakeQuery([existing])
        raise AssertionError(f"Unexpected MCP query: {query}")

    def _fake_a2a_find(*_args, **_kwargs):
        return _FakeQuery([])

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedMCPServer.find", _fake_mcp_find)
    monkeypatch.setattr("registry.services.federation_sync_service.A2AAgent.find", _fake_a2a_find)

    result = await federation_sync_service._build_sync_plan(
        federation=federation, discovered_mcp=[discovered], discovered_a2a=[]
    )

    assert result.summary.unchangedMcpServers == 1
    assert result.summary.updatedMcpServers == 0
    assert existing_id in result.mcp_pre_existing_acl_targets


@pytest.mark.asyncio
async def test_build_sync_plan_mcp_rename_self_conflict_is_no_op(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    """When an MCP server's serverName matches its own persisted doc, that is not a conflict."""
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    runtime_arn = "arn:aws:bedrock-agentcore:us-east-1:123:runtime/self-rename"
    existing_id = PydanticObjectId()
    existing = SimpleNamespace(
        id=existing_id,
        federationRefId=federation.id,
        federationMetadata={"runtimeArn": runtime_arn, "runtimeVersion": "1"},
        serverName="old-name",
        config={"runtimeAccess": {"mode": "iam"}},
    )
    discovered = SimpleNamespace(
        serverName="new-name",
        federationMetadata={"runtimeArn": runtime_arn, "runtimeVersion": "2"},
        config={"runtimeAccess": {"mode": "iam"}},
    )

    persisted_self = SimpleNamespace(
        id=existing_id,
        serverName="new-name",
        federationRefId=federation.id,
        federationMetadata={"runtimeArn": runtime_arn},
    )

    def _fake_mcp_find(query, session=None):
        if "federationRefId" in query:
            return _FakeQuery([existing])
        if "serverName" in query:
            return _FakeQuery([persisted_self])
        raise AssertionError(f"Unexpected MCP query: {query}")

    def _fake_a2a_find(*_args, **_kwargs):
        return _FakeQuery([])

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedMCPServer.find", _fake_mcp_find)
    monkeypatch.setattr("registry.services.federation_sync_service.A2AAgent.find", _fake_a2a_find)

    result = await federation_sync_service._build_sync_plan(
        federation=federation, discovered_mcp=[discovered], discovered_a2a=[]
    )

    assert result.summary.skippedMcpServers == 0
    assert result.summary.updatedMcpServers == 1
    assert result.summary.errors == 0
