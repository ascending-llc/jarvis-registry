"""Tests for federation ACL inheritance."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId

from registry.services.federation_sync_service import (
    ACL_INHERITANCE_BATCH_SIZE,
    FederationSyncPlan,
    FederationSyncService,
)
from registry_pkgs.models import PrincipalType, ResourceType
from registry_pkgs.models.enums import (
    FederationProviderType,
    RoleBits,
)
from registry_pkgs.models.extended_access_role import RegistryResourceType
from registry_pkgs.models.extended_acl_entry import RegistryAclEntry
from registry_pkgs.models.federation_sync_job import FederationApplySummary
from registry_pkgs.testing.federation_metadata import (
    make_agentcore_a2a_metadata,
    make_agentcore_mcp_metadata,
)
from tests.unit.services.federation_sync_test_helpers import (
    _FakeQuery,
    _make_federation,
)

pytestmark = pytest.mark.usefixtures("default_empty_access_roles")


def _make_sync_plan(
    federation_id, provider_type, mcp_creates=None, mcp_updates=None, a2a_creates=None, a2a_updates=None
):
    from registry.services.federation_sync_service import FederationSyncPlan

    mcp_creates = mcp_creates or []
    mcp_updates = mcp_updates or []
    a2a_creates = a2a_creates or []
    a2a_updates = a2a_updates or []

    return FederationSyncPlan(
        federation_id=federation_id,
        provider_type=provider_type,
        summary=FederationApplySummary(),
        discovered_mcp_count=len(mcp_creates) + len(mcp_updates),
        discovered_a2a_count=len(a2a_creates) + len(a2a_updates),
        mcp_creates=mcp_creates,
        mcp_updates=mcp_updates,
        mcp_deletes=[],
        a2a_creates=a2a_creates,
        a2a_updates=a2a_updates,
        a2a_deletes=[],
    )


def _make_acl_entry(
    principal_type: str,
    principal_id: str,
    resource_type: str,
    resource_id: PydanticObjectId,
    perm_bits: int,
    role_id: PydanticObjectId | None = None,
):
    """Helper to create a mock ACL entry for testing."""
    now = datetime.now(UTC)
    entry = MagicMock(spec=RegistryAclEntry)
    entry.id = PydanticObjectId()
    entry.principalType = principal_type
    entry.principalId = principal_id
    entry.resourceType = resource_type
    entry.resourceId = resource_id
    entry.roleId = role_id
    entry.permBits = perm_bits
    entry.grantedAt = now
    entry.createdAt = now
    entry.updatedAt = now
    return entry


def _make_access_role(resource_type: str, perm_bits: int, access_role_id: str):
    return SimpleNamespace(
        id=PydanticObjectId(),
        resourceType=resource_type,
        permBits=perm_bits,
        accessRoleId=access_role_id,
    )


def _patch_acl_inheritance_storage(
    monkeypatch,
    *,
    existing_acl_entries: list[RegistryAclEntry],
    inserted_entries: list[RegistryAclEntry],
    insert_calls: list[list[RegistryAclEntry]] | None = None,
) -> None:
    class MockRegistryAclEntry:
        @staticmethod
        def find(query, **kwargs):
            if "$or" in query or "resourceId" in query:
                return _FakeQuery(existing_acl_entries)
            return _FakeQuery([])

        @staticmethod
        async def insert_many(entries, **kwargs):
            inserted_entries.extend(entries)
            if insert_calls is not None:
                insert_calls.append(entries)

        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    monkeypatch.setattr("registry.services.federation_sync_service.RegistryAclEntry", MockRegistryAclEntry)


@pytest.mark.asyncio
async def test_build_sync_plan_tracks_unchanged_resources_for_acl_inheritance(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    mcp_id = PydanticObjectId()
    a2a_id = PydanticObjectId()
    mcp_runtime_arn = "arn:aws:bedrock-agentcore:us-east-1:123:runtime/unchanged-mcp"
    a2a_runtime_arn = "arn:aws:bedrock-agentcore:us-east-1:123:runtime/unchanged-a2a"

    existing_mcp = SimpleNamespace(
        id=mcp_id,
        serverName="unchanged-mcp",
        federationRefId=federation.id,
        federationMetadata=make_agentcore_mcp_metadata(runtime_arn=mcp_runtime_arn, runtime_version="1"),
    )
    discovered_mcp = SimpleNamespace(
        id=PydanticObjectId(),
        serverName="unchanged-mcp",
        federationMetadata=make_agentcore_mcp_metadata(runtime_arn=mcp_runtime_arn, runtime_version="1"),
    )
    existing_a2a = SimpleNamespace(
        id=a2a_id,
        path="/agentcore/a2a/unchanged-a2a",
        config=SimpleNamespace(type="jsonrpc"),
        federationRefId=federation.id,
        federationMetadata=make_agentcore_a2a_metadata(runtime_arn=a2a_runtime_arn, runtime_version="1"),
    )
    discovered_a2a = SimpleNamespace(
        id=PydanticObjectId(),
        path="/agentcore/a2a/unchanged-a2a",
        card=SimpleNamespace(name="unchanged-a2a"),
        config=SimpleNamespace(type="jsonrpc"),
        federationMetadata=make_agentcore_a2a_metadata(runtime_arn=a2a_runtime_arn, runtime_version="1"),
    )

    def _fake_mcp_find(query, session=None):
        if "federationRefId" in query:
            return _FakeQuery([existing_mcp])
        if "serverName" in query:
            return _FakeQuery([])
        raise AssertionError(f"unexpected MCP query: {query}")

    def _fake_a2a_find(query, session=None):
        if "federationRefId" in query:
            return _FakeQuery([existing_a2a])
        if "path" in query:
            return _FakeQuery([existing_a2a])
        raise AssertionError(f"unexpected A2A query: {query}")

    monkeypatch.setattr("registry.services.federation_sync_service.ExtendedMCPServer.find", _fake_mcp_find)
    monkeypatch.setattr("registry.services.federation_sync_service.A2AAgent.find", _fake_a2a_find)

    sync_plan = await federation_sync_service._build_sync_plan(
        federation=federation,
        discovered_mcp=[discovered_mcp],
        discovered_a2a=[discovered_a2a],
    )

    assert sync_plan.summary.unchangedMcpServers == 1
    assert sync_plan.summary.unchangedAgents == 1
    assert sync_plan.mcp_pre_existing_acl_targets == [mcp_id]
    assert sync_plan.a2a_pre_existing_acl_targets == [a2a_id]


@pytest.mark.asyncio
async def test_apply_sync_plan_inherits_acl_to_unchanged_mcp_and_a2a_resources(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation_id = PydanticObjectId()
    mcp_id = PydanticObjectId()
    a2a_id = PydanticObjectId()
    federation_acl_entries = [
        _make_acl_entry(PrincipalType.USER, "kent", RegistryResourceType.FEDERATION, federation_id, RoleBits.OWNER)
    ]
    sync_plan = FederationSyncPlan(
        summary=FederationApplySummary(unchangedMcpServers=1, unchangedAgents=1),
        federation_id=federation_id,
        provider_type=FederationProviderType.AWS_AGENTCORE,
        discovered_mcp_count=1,
        discovered_a2a_count=1,
        mcp_pre_existing_acl_targets=[mcp_id],
        a2a_pre_existing_acl_targets=[a2a_id],
    )

    federation_sync_service._get_federation_acl_entries = AsyncMock(return_value=(federation_acl_entries, True))
    federation_sync_service._batch_inherit_federation_acl = AsyncMock()

    await federation_sync_service._apply_sync_plan(sync_plan, session=None)

    federation_sync_service._batch_inherit_federation_acl.assert_awaited_once_with(
        federation_acl_entries=federation_acl_entries,
        resources=[
            (ResourceType.MCPSERVER, mcp_id),
            (ResourceType.REMOTE_AGENT, a2a_id),
        ],
        session=None,
    )


@pytest.mark.asyncio
async def test_apply_sync_plan_updates_a2a_runtime_access(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation_id = PydanticObjectId()
    existing_a2a = SimpleNamespace(
        id=PydanticObjectId(),
        path="/agentcore/a2a/auth-mode-a2a",
        card=SimpleNamespace(name="auth-mode-a2a"),
        tags=[],
        wellKnown=None,
        config=SimpleNamespace(type="http_json", runtimeAccess=SimpleNamespace(mode="iam"), enabled=True),
        federationMetadata=make_agentcore_a2a_metadata(runtime_version="1"),
        save=AsyncMock(),
    )
    updated_runtime_access = SimpleNamespace(mode="jwt")
    update_a2a_item = SimpleNamespace(
        path="/agentcore/a2a/auth-mode-a2a",
        card=SimpleNamespace(name="auth-mode-a2a"),
        tags=[],
        wellKnown=None,
        config=SimpleNamespace(type="jsonrpc", runtimeAccess=updated_runtime_access, enabled=True),
        federationMetadata=make_agentcore_a2a_metadata(runtime_version="1"),
    )
    sync_plan = FederationSyncPlan(
        summary=FederationApplySummary(updatedAgents=1),
        federation_id=federation_id,
        provider_type=FederationProviderType.AWS_AGENTCORE,
        discovered_mcp_count=0,
        discovered_a2a_count=1,
        a2a_updates=[(existing_a2a, update_a2a_item, "arn:updated-a2a")],
    )

    federation_sync_service._get_federation_acl_entries = AsyncMock(return_value=([], True))
    federation_sync_service._batch_inherit_federation_acl = AsyncMock()

    await federation_sync_service._apply_sync_plan(sync_plan, session=None)

    assert existing_a2a.config.runtimeAccess is updated_runtime_access
    assert existing_a2a.config.runtimeAccess.mode == "jwt"
    assert existing_a2a.config.type == update_a2a_item.config.type
    existing_a2a.save.assert_awaited_once_with(session=None)
    federation_sync_service._batch_inherit_federation_acl.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_sync_plan_inherits_acl_to_created_updated_and_unchanged_resources(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    """
    Test that ACL inheritance applies to:
    - Pre-existing unchanged resources
    - Newly created resources (after insert)
    - Updated resources (after save)
    But NOT to deleted resources.
    """
    federation_id = PydanticObjectId()
    unchanged_mcp_id = PydanticObjectId()
    unchanged_a2a_id = PydanticObjectId()

    # Mock resources for creates - these will have IDs set after insert
    new_mcp_id = PydanticObjectId()
    new_a2a_id = PydanticObjectId()

    # Mock resources for updates
    existing_mcp = SimpleNamespace(
        id=PydanticObjectId(),
        serverName="existing-mcp",
        path="/path",
        tags=[],
        config={},
        numTools=1,
        federationMetadata=None,
        save=AsyncMock(),
    )
    existing_a2a = SimpleNamespace(
        id=PydanticObjectId(),
        path="/path",
        card=SimpleNamespace(name="existing"),
        tags=[],
        wellKnown=None,
        config=SimpleNamespace(type="jsonrpc", enabled=True),
        federationMetadata=None,
        save=AsyncMock(),
    )

    # Mock resources for deletes
    deleted_mcp = SimpleNamespace(id=PydanticObjectId(), delete=AsyncMock())
    deleted_a2a = SimpleNamespace(id=PydanticObjectId(), delete=AsyncMock())

    # Mock new items for creates/updates
    new_mcp_item = SimpleNamespace(
        serverName="new-mcp",
        path="/new-path",
        tags=[],
        config={},
        numTools=1,
        federationMetadata=None,
    )
    new_a2a_item = SimpleNamespace(
        path="/new-path",
        card=SimpleNamespace(name="new"),
        tags=[],
        wellKnown=None,
        config=SimpleNamespace(type="jsonrpc", enabled=True),
        federationMetadata=None,
    )

    # Mock insert to set IDs on the items
    async def mock_mcp_insert(**kwargs):
        new_mcp_item.id = new_mcp_id

    async def mock_a2a_insert(**kwargs):
        new_a2a_item.id = new_a2a_id

    new_mcp_item.insert = AsyncMock(side_effect=mock_mcp_insert)
    new_a2a_item.insert = AsyncMock(side_effect=mock_a2a_insert)

    update_mcp_item = SimpleNamespace(
        serverName="updated-mcp",
        path="/updated-path",
        tags=[],
        config={},
        numTools=2,
        federationMetadata=None,
    )
    update_a2a_item = SimpleNamespace(
        path="/updated-path",
        card=SimpleNamespace(name="updated"),
        tags=[],
        wellKnown=None,
        config=SimpleNamespace(type="jsonrpc", enabled=True),
        federationMetadata=None,
    )

    federation_acl_entries = [
        _make_acl_entry(PrincipalType.USER, "kent", RegistryResourceType.FEDERATION, federation_id, RoleBits.OWNER)
    ]

    sync_plan = FederationSyncPlan(
        summary=FederationApplySummary(
            unchangedMcpServers=1,
            unchangedAgents=1,
            createdMcpServers=1,
            createdAgents=1,
            updatedMcpServers=1,
            updatedAgents=1,
            deletedMcpServers=1,
            deletedAgents=1,
        ),
        federation_id=federation_id,
        provider_type=FederationProviderType.AWS_AGENTCORE,
        discovered_mcp_count=3,
        discovered_a2a_count=3,
        mcp_pre_existing_acl_targets=[unchanged_mcp_id],
        a2a_pre_existing_acl_targets=[unchanged_a2a_id],
        mcp_creates=[(new_mcp_item, "arn:new-mcp")],
        a2a_creates=[(new_a2a_item, "arn:new-a2a")],
        mcp_updates=[(existing_mcp, update_mcp_item, "arn:updated-mcp")],
        a2a_updates=[(existing_a2a, update_a2a_item, "arn:updated-a2a")],
        mcp_deletes=[(deleted_mcp, "arn:deleted-mcp")],
        a2a_deletes=[(deleted_a2a, "arn:deleted-a2a")],
    )

    federation_sync_service._get_federation_acl_entries = AsyncMock(return_value=(federation_acl_entries, True))
    federation_sync_service._batch_inherit_federation_acl = AsyncMock()

    await federation_sync_service._apply_sync_plan(sync_plan, session=None)

    # Verify that _batch_inherit_federation_acl was called
    federation_sync_service._batch_inherit_federation_acl.assert_awaited_once()

    # Get the resources argument from the call
    call_args = federation_sync_service._batch_inherit_federation_acl.await_args
    resources = call_args.kwargs["resources"]

    # Extract resource IDs from the resources list
    resource_ids = {str(resource_id) for _, resource_id in resources}

    # Should include: unchanged (2), created (2), updated (2) = 6 total
    assert len(resources) == 6

    # Should include unchanged resources
    assert str(unchanged_mcp_id) in resource_ids
    assert str(unchanged_a2a_id) in resource_ids

    # Should include created resources (after insert sets their IDs)
    assert str(new_mcp_id) in resource_ids
    assert str(new_a2a_id) in resource_ids

    # Should include updated resources
    assert str(existing_mcp.id) in resource_ids
    assert str(existing_a2a.id) in resource_ids

    # Should NOT include deleted resources
    assert str(deleted_mcp.id) not in resource_ids
    assert str(deleted_a2a.id) not in resource_ids


@pytest.mark.asyncio
async def test_apply_sync_plan_raises_when_federation_acl_query_fails(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    sync_plan = FederationSyncPlan(
        summary=FederationApplySummary(),
        federation_id=PydanticObjectId(),
        provider_type=FederationProviderType.AWS_AGENTCORE,
        discovered_mcp_count=0,
        discovered_a2a_count=0,
    )

    federation_sync_service._get_federation_acl_entries = AsyncMock(return_value=([], False))
    federation_sync_service._batch_inherit_federation_acl = AsyncMock()

    with pytest.raises(RuntimeError, match="could not query federation ACL"):
        await federation_sync_service._apply_sync_plan(sync_plan, session=None)

    federation_sync_service._batch_inherit_federation_acl.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_federation_acl_entries_uses_current_transaction_session(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation_id = PydanticObjectId()
    session = object()
    find_calls = []

    def mock_find(query, **kwargs):
        find_calls.append({"query": query, "kwargs": kwargs})
        return _FakeQuery([])

    monkeypatch.setattr("registry.services.federation_sync_service.RegistryAclEntry.find", mock_find)

    entries, query_success = await federation_sync_service._get_federation_acl_entries(federation_id, session=session)

    assert entries == []
    assert query_success is True
    assert find_calls == [
        {
            "query": {
                "resourceType": RegistryResourceType.FEDERATION,
                "resourceId": federation_id,
                "principalType": {"$ne": PrincipalType.PUBLIC.value},
                "principalId": {"$ne": None},
            },
            "kwargs": {"session": session},
        }
    ]


@pytest.mark.asyncio
async def test_batch_inherit_acl_normalizes_resource_type_before_existence_check(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation_id = PydanticObjectId()
    server_id = PydanticObjectId()
    federation_acl_entries = [
        _make_acl_entry(PrincipalType.USER, "kent", RegistryResourceType.FEDERATION, federation_id, RoleBits.OWNER)
    ]
    existing_resource_acl = [
        _make_acl_entry(
            PrincipalType.USER,
            "kent",
            RegistryResourceType.MCP_SERVER,
            server_id,
            RoleBits.EDITOR,
        )
    ]
    inserted_entries = []

    class MockRegistryAclEntry:
        @staticmethod
        def find(query, **kwargs):
            if "$or" in query or "resourceId" in query:
                return _FakeQuery(existing_resource_acl)
            return _FakeQuery([])

        @staticmethod
        async def insert_many(entries, **kwargs):
            inserted_entries.extend(entries)

        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    monkeypatch.setattr("registry.services.federation_sync_service.RegistryAclEntry", MockRegistryAclEntry)

    await federation_sync_service._batch_inherit_federation_acl(
        federation_acl_entries=federation_acl_entries,
        resources=[(ResourceType.MCPSERVER, server_id)],
    )

    assert inserted_entries == []


@pytest.mark.asyncio
async def test_batch_inherit_acl_rewrites_role_id_to_target_resource_type(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation_id = PydanticObjectId()
    server_id = PydanticObjectId()
    federation_editor_role = _make_access_role(
        RegistryResourceType.FEDERATION,
        RoleBits.EDITOR,
        "federation_editor",
    )
    mcp_editor_role = _make_access_role(ResourceType.MCPSERVER, RoleBits.EDITOR, "mcpServer_editor")
    federation_acl_entries = [
        _make_acl_entry(
            PrincipalType.USER,
            "kent",
            RegistryResourceType.FEDERATION,
            federation_id,
            RoleBits.EDITOR,
            role_id=federation_editor_role.id,
        )
    ]
    inserted_entries = []

    monkeypatch.setattr(
        "registry.services.federation_sync_service.RegistryAccessRole.find",
        lambda *args, **kwargs: _FakeQuery([mcp_editor_role]),
    )
    _patch_acl_inheritance_storage(
        monkeypatch,
        existing_acl_entries=[],
        inserted_entries=inserted_entries,
    )

    await federation_sync_service._batch_inherit_federation_acl(
        federation_acl_entries=federation_acl_entries,
        resources=[(ResourceType.MCPSERVER, server_id)],
    )

    assert len(inserted_entries) == 1
    assert inserted_entries[0].roleId == mcp_editor_role.id
    assert inserted_entries[0].roleId != federation_editor_role.id


@pytest.mark.asyncio
async def test_batch_inherit_acl_uses_role_id_scoped_to_each_resource_type(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation_id = PydanticObjectId()
    server_id = PydanticObjectId()
    agent_id = PydanticObjectId()
    federation_editor_role = _make_access_role(
        RegistryResourceType.FEDERATION,
        RoleBits.EDITOR,
        "federation_editor",
    )
    mcp_editor_role = _make_access_role(ResourceType.MCPSERVER, RoleBits.EDITOR, "mcpServer_editor")
    remote_agent_editor_role = _make_access_role(
        ResourceType.REMOTE_AGENT,
        RoleBits.EDITOR,
        "remoteAgent_editor",
    )
    federation_acl_entries = [
        _make_acl_entry(
            PrincipalType.USER,
            "kent",
            RegistryResourceType.FEDERATION,
            federation_id,
            RoleBits.EDITOR,
            role_id=federation_editor_role.id,
        )
    ]
    inserted_entries = []

    monkeypatch.setattr(
        "registry.services.federation_sync_service.RegistryAccessRole.find",
        lambda *args, **kwargs: _FakeQuery([mcp_editor_role, remote_agent_editor_role]),
    )
    _patch_acl_inheritance_storage(
        monkeypatch,
        existing_acl_entries=[],
        inserted_entries=inserted_entries,
    )

    await federation_sync_service._batch_inherit_federation_acl(
        federation_acl_entries=federation_acl_entries,
        resources=[(ResourceType.MCPSERVER, server_id), (ResourceType.REMOTE_AGENT, agent_id)],
    )

    inserted_by_resource_type = {entry.resourceType: entry for entry in inserted_entries}
    assert inserted_by_resource_type[ResourceType.MCPSERVER].roleId == mcp_editor_role.id
    assert inserted_by_resource_type[ResourceType.REMOTE_AGENT].roleId == remote_agent_editor_role.id


@pytest.mark.asyncio
async def test_batch_inherit_acl_sets_role_id_none_when_target_role_is_missing(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation_id = PydanticObjectId()
    server_id = PydanticObjectId()
    federation_acl_entries = [
        _make_acl_entry(
            PrincipalType.USER,
            "kent",
            RegistryResourceType.FEDERATION,
            federation_id,
            7,
            role_id=PydanticObjectId(),
        )
    ]
    inserted_entries = []

    _patch_acl_inheritance_storage(
        monkeypatch,
        existing_acl_entries=[],
        inserted_entries=inserted_entries,
    )

    await federation_sync_service._batch_inherit_federation_acl(
        federation_acl_entries=federation_acl_entries,
        resources=[(ResourceType.MCPSERVER, server_id)],
    )

    assert len(inserted_entries) == 1
    assert inserted_entries[0].roleId is None


@pytest.mark.asyncio
async def test_batch_inherit_acl_copies_perm_bits_when_target_role_is_missing(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation_id = PydanticObjectId()
    server_id = PydanticObjectId()
    federation_acl_entries = [
        _make_acl_entry(
            PrincipalType.USER,
            "kent",
            RegistryResourceType.FEDERATION,
            federation_id,
            7,
            role_id=PydanticObjectId(),
        )
    ]
    inserted_entries = []

    _patch_acl_inheritance_storage(
        monkeypatch,
        existing_acl_entries=[],
        inserted_entries=inserted_entries,
    )

    await federation_sync_service._batch_inherit_federation_acl(
        federation_acl_entries=federation_acl_entries,
        resources=[(ResourceType.MCPSERVER, server_id)],
    )

    assert len(inserted_entries) == 1
    assert inserted_entries[0].permBits == 7


@pytest.mark.asyncio
async def test_batch_inherit_acl_insert_only_semantics_are_unchanged_by_role_lookup(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation_id = PydanticObjectId()
    server_id = PydanticObjectId()
    principal_id = "kent"
    federation_acl_entries = [
        _make_acl_entry(
            PrincipalType.USER,
            principal_id,
            RegistryResourceType.FEDERATION,
            federation_id,
            RoleBits.EDITOR,
            role_id=PydanticObjectId(),
        )
    ]
    existing_resource_acl = [
        _make_acl_entry(
            PrincipalType.USER,
            principal_id,
            ResourceType.MCPSERVER,
            server_id,
            RoleBits.VIEWER,
        )
    ]
    inserted_entries = []
    insert_calls = []

    monkeypatch.setattr(
        "registry.services.federation_sync_service.RegistryAccessRole.find",
        lambda *args, **kwargs: _FakeQuery(
            [_make_access_role(ResourceType.MCPSERVER, RoleBits.EDITOR, "mcpServer_editor")]
        ),
    )
    _patch_acl_inheritance_storage(
        monkeypatch,
        existing_acl_entries=existing_resource_acl,
        inserted_entries=inserted_entries,
        insert_calls=insert_calls,
    )

    await federation_sync_service._batch_inherit_federation_acl(
        federation_acl_entries=federation_acl_entries,
        resources=[(ResourceType.MCPSERVER, server_id)],
    )

    assert inserted_entries == []
    assert insert_calls == []


@pytest.mark.asyncio
async def test_federation_acl_inheritance_scenario_1_empty_resource(
    federation_sync_service: FederationSyncService, monkeypatch
):
    """
    Scenario 1: Federation has ACL, resource is empty → inherit all.

    Federation ACL: Kent=owner, Ryo=editor, Celeste=viewer
    Resource ACL before: empty
    Resource ACL after: Kent=owner, Ryo=editor, Celeste=viewer
    """
    federation_id = PydanticObjectId()
    server_id = PydanticObjectId()

    # Federation ACL entries
    federation_acl_entries = [
        _make_acl_entry(PrincipalType.USER, "kent", RegistryResourceType.FEDERATION, federation_id, RoleBits.OWNER),
        _make_acl_entry(PrincipalType.USER, "ryo", RegistryResourceType.FEDERATION, federation_id, RoleBits.EDITOR),
        _make_acl_entry(PrincipalType.USER, "celeste", RegistryResourceType.FEDERATION, federation_id, RoleBits.VIEWER),
    ]

    # Mock: Federation has 3 ACL entries, resource has 0
    inserted_entries = []

    # Create a mock class that has both class methods and constructor
    class MockRegistryAclEntry:
        @staticmethod
        def find(query, **kwargs):
            # Check for Federation ACL query
            if query.get("resourceType") == RegistryResourceType.FEDERATION:
                return _FakeQuery(federation_acl_entries)
            # Check for resource ACL query (either $or or single resource filter)
            if "$or" in query or "resourceId" in query:
                return _FakeQuery([])  # Resource has no existing ACL
            return _FakeQuery([])

        @staticmethod
        async def insert_many(entries, **kwargs):
            inserted_entries.extend(entries)

        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    monkeypatch.setattr("registry.services.federation_sync_service.RegistryAclEntry", MockRegistryAclEntry)

    # Execute
    await federation_sync_service._batch_inherit_federation_acl(
        federation_acl_entries=federation_acl_entries,
        resources=[(ResourceType.MCPSERVER, server_id)],
    )

    # Verify: All 3 ACL entries should be inserted
    assert len(inserted_entries) == 3
    assert {e.principalId for e in inserted_entries} == {"kent", "ryo", "celeste"}
    assert {e.permBits for e in inserted_entries} == {RoleBits.OWNER, RoleBits.EDITOR, RoleBits.VIEWER}


@pytest.mark.asyncio
async def test_federation_acl_inheritance_scenario_2_preserve_existing(
    federation_sync_service: FederationSyncService, monkeypatch
):
    """
    Scenario 2: Federation has ACL, resource has other users → inherit + preserve.

    Federation ACL: Kent=owner, Ryo=editor, Celeste=viewer
    Resource ACL before: Alex=owner
    Resource ACL after: Kent=owner, Ryo=editor, Celeste=viewer, Alex=owner (preserved)
    """
    federation_id = PydanticObjectId()
    server_id = PydanticObjectId()

    federation_acl_entries = [
        _make_acl_entry(PrincipalType.USER, "kent", RegistryResourceType.FEDERATION, federation_id, RoleBits.OWNER),
        _make_acl_entry(PrincipalType.USER, "ryo", RegistryResourceType.FEDERATION, federation_id, RoleBits.EDITOR),
        _make_acl_entry(PrincipalType.USER, "celeste", RegistryResourceType.FEDERATION, federation_id, RoleBits.VIEWER),
    ]

    # Resource already has Alex as owner
    existing_resource_acl = [
        _make_acl_entry(PrincipalType.USER, "alex", ResourceType.MCPSERVER, server_id, RoleBits.OWNER)
    ]

    inserted_entries = []

    # Create a mock class that has both class methods and constructor
    class MockRegistryAclEntry:
        @staticmethod
        def find(query, **kwargs):
            # Check for Federation ACL query
            if query.get("resourceType") == RegistryResourceType.FEDERATION:
                return _FakeQuery(federation_acl_entries)
            # Check for resource ACL query (either $or or single resource filter)
            if "$or" in query or "resourceId" in query:
                return _FakeQuery(existing_resource_acl)
            return _FakeQuery([])

        @staticmethod
        async def insert_many(entries, **kwargs):
            inserted_entries.extend(entries)

        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    monkeypatch.setattr("registry.services.federation_sync_service.RegistryAclEntry", MockRegistryAclEntry)

    await federation_sync_service._batch_inherit_federation_acl(
        federation_acl_entries=federation_acl_entries,
        resources=[(ResourceType.MCPSERVER, server_id)],
    )

    # Verify: Only Kent, Ryo, Celeste should be inserted (Alex is preserved)
    assert len(inserted_entries) == 3
    assert {e.principalId for e in inserted_entries} == {"kent", "ryo", "celeste"}
    assert "alex" not in {e.principalId for e in inserted_entries}


@pytest.mark.asyncio
async def test_federation_acl_inheritance_scenario_3_higher_permission_preserved(
    federation_sync_service: FederationSyncService, monkeypatch
):
    """
    Scenario 3: User has higher permission on resource → preserve existing.

    Federation ACL: Kent=owner, Ryo=editor, Celeste=viewer
    Resource ACL before: Ryo=owner
    Resource ACL after: Kent=owner, Ryo=owner (preserved, NOT downgraded), Celeste=viewer
    """
    federation_id = PydanticObjectId()
    server_id = PydanticObjectId()

    federation_acl_entries = [
        _make_acl_entry(PrincipalType.USER, "kent", RegistryResourceType.FEDERATION, federation_id, RoleBits.OWNER),
        _make_acl_entry(PrincipalType.USER, "ryo", RegistryResourceType.FEDERATION, federation_id, RoleBits.EDITOR),
        _make_acl_entry(PrincipalType.USER, "celeste", RegistryResourceType.FEDERATION, federation_id, RoleBits.VIEWER),
    ]

    # Ryo already has OWNER on the resource
    existing_resource_acl = [
        _make_acl_entry(PrincipalType.USER, "ryo", ResourceType.MCPSERVER, server_id, RoleBits.OWNER)
    ]

    inserted_entries = []

    # Create a mock class that has both class methods and constructor
    class MockRegistryAclEntry:
        @staticmethod
        def find(query, **kwargs):
            # Check for Federation ACL query
            if query.get("resourceType") == RegistryResourceType.FEDERATION:
                return _FakeQuery(federation_acl_entries)
            # Check for resource ACL query (either $or or single resource filter)
            if "$or" in query or "resourceId" in query:
                return _FakeQuery(existing_resource_acl)
            return _FakeQuery([])

        @staticmethod
        async def insert_many(entries, **kwargs):
            inserted_entries.extend(entries)

        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    monkeypatch.setattr("registry.services.federation_sync_service.RegistryAclEntry", MockRegistryAclEntry)

    await federation_sync_service._batch_inherit_federation_acl(
        federation_acl_entries=federation_acl_entries,
        resources=[(ResourceType.MCPSERVER, server_id)],
    )

    # Verify: Only Kent and Celeste should be inserted (Ryo already exists)
    assert len(inserted_entries) == 2
    assert {e.principalId for e in inserted_entries} == {"kent", "celeste"}
    assert "ryo" not in {e.principalId for e in inserted_entries}


@pytest.mark.asyncio
async def test_federation_acl_inheritance_scenario_4_lower_permission_preserved(
    federation_sync_service: FederationSyncService, monkeypatch
):
    """
    Scenario 4: User has lower permission on resource → preserve existing (no upgrade).

    Federation ACL: Kent=owner, Ryo=editor, Celeste=viewer
    Resource ACL before: Kent=editor
    Resource ACL after: Kent=editor (preserved, NOT upgraded), Ryo=editor, Celeste=viewer
    """
    federation_id = PydanticObjectId()
    server_id = PydanticObjectId()

    federation_acl_entries = [
        _make_acl_entry(PrincipalType.USER, "kent", RegistryResourceType.FEDERATION, federation_id, RoleBits.OWNER),
        _make_acl_entry(PrincipalType.USER, "ryo", RegistryResourceType.FEDERATION, federation_id, RoleBits.EDITOR),
        _make_acl_entry(PrincipalType.USER, "celeste", RegistryResourceType.FEDERATION, federation_id, RoleBits.VIEWER),
    ]

    # Kent already has EDITOR (lower than OWNER in Federation)
    existing_resource_acl = [
        _make_acl_entry(PrincipalType.USER, "kent", ResourceType.MCPSERVER, server_id, RoleBits.EDITOR)
    ]

    inserted_entries = []

    # Create a mock class that has both class methods and constructor
    class MockRegistryAclEntry:
        @staticmethod
        def find(query, **kwargs):
            # Check for Federation ACL query
            if query.get("resourceType") == RegistryResourceType.FEDERATION:
                return _FakeQuery(federation_acl_entries)
            # Check for resource ACL query (either $or or single resource filter)
            if "$or" in query or "resourceId" in query:
                return _FakeQuery(existing_resource_acl)
            return _FakeQuery([])

        @staticmethod
        async def insert_many(entries, **kwargs):
            inserted_entries.extend(entries)

        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    monkeypatch.setattr("registry.services.federation_sync_service.RegistryAclEntry", MockRegistryAclEntry)

    await federation_sync_service._batch_inherit_federation_acl(
        federation_acl_entries=federation_acl_entries,
        resources=[(ResourceType.MCPSERVER, server_id)],
    )

    # Verify: Only Ryo and Celeste should be inserted (Kent already exists, NOT upgraded)
    assert len(inserted_entries) == 2
    assert {e.principalId for e in inserted_entries} == {"ryo", "celeste"}
    assert "kent" not in {e.principalId for e in inserted_entries}


@pytest.mark.asyncio
async def test_federation_acl_inheritance_validates_principal_id(
    federation_sync_service: FederationSyncService, monkeypatch
):
    """
    ACL entries with null principalId should be skipped with a warning.
    """
    federation_id = PydanticObjectId()
    server_id = PydanticObjectId()

    # Create ACL entries, including one with None principalId
    valid_entry = _make_acl_entry(
        PrincipalType.USER, "kent", RegistryResourceType.FEDERATION, federation_id, RoleBits.OWNER
    )

    invalid_entry = _make_acl_entry(
        PrincipalType.USER, "invalid", RegistryResourceType.FEDERATION, federation_id, RoleBits.EDITOR
    )
    invalid_entry.principalId = None  # Simulate invalid entry

    federation_acl_entries = [valid_entry, invalid_entry]

    inserted_entries = []

    # Create a mock class that has both class methods and constructor
    class MockRegistryAclEntry:
        @staticmethod
        def find(query, **kwargs):
            if query.get("resourceType") == RegistryResourceType.FEDERATION:
                return _FakeQuery(federation_acl_entries)
            if "$or" in query or "resourceId" in query:
                return _FakeQuery([])
            return _FakeQuery([])

        @staticmethod
        async def insert_many(entries, **kwargs):
            inserted_entries.extend(entries)

        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    monkeypatch.setattr("registry.services.federation_sync_service.RegistryAclEntry", MockRegistryAclEntry)

    await federation_sync_service._batch_inherit_federation_acl(
        federation_acl_entries=federation_acl_entries,
        resources=[(ResourceType.MCPSERVER, server_id)],
    )

    # Verify: Only valid entry should be inserted
    assert len(inserted_entries) == 1
    assert inserted_entries[0].principalId == "kent"


@pytest.mark.asyncio
async def test_batch_inherit_acl_uses_batched_insert(federation_sync_service: FederationSyncService, monkeypatch):
    """
    Large batches of ACL entries should be inserted in chunks (500 per batch).
    Verify that insert_many is called with ordered=False for idempotency.
    """
    federation_id = PydanticObjectId()

    # Create 3 federation ACL entries
    federation_acl_entries = [
        _make_acl_entry(PrincipalType.USER, "user1", RegistryResourceType.FEDERATION, federation_id, RoleBits.OWNER),
        _make_acl_entry(PrincipalType.USER, "user2", RegistryResourceType.FEDERATION, federation_id, RoleBits.EDITOR),
        _make_acl_entry(PrincipalType.USER, "user3", RegistryResourceType.FEDERATION, federation_id, RoleBits.VIEWER),
    ]

    # Create 200 resources (will generate 600 ACL entries, split into 2 batches)
    resources = [(ResourceType.MCPSERVER, PydanticObjectId()) for _ in range(200)]

    insert_calls = []

    # Create a mock class that has both class methods and constructor
    class MockRegistryAclEntry:
        @staticmethod
        def find(query, **kwargs):
            if query.get("resourceType") == RegistryResourceType.FEDERATION:
                return _FakeQuery(federation_acl_entries)
            if "$or" in query or "resourceId" in query:
                return _FakeQuery([])  # No existing ACL
            return _FakeQuery([])

        @staticmethod
        async def insert_many(entries, **kwargs):
            insert_calls.append({"count": len(entries), "kwargs": kwargs})

        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    monkeypatch.setattr("registry.services.federation_sync_service.RegistryAclEntry", MockRegistryAclEntry)

    await federation_sync_service._batch_inherit_federation_acl(
        federation_acl_entries=federation_acl_entries,
        resources=resources,
    )

    # Verify: Should have 2 insert calls (600 entries / batch size = 2 batches)
    assert len(insert_calls) == 2
    assert insert_calls[0]["count"] == ACL_INHERITANCE_BATCH_SIZE
    assert insert_calls[1]["count"] == len(resources) * len(federation_acl_entries) - ACL_INHERITANCE_BATCH_SIZE

    # Verify: All calls use ordered=False
    for call in insert_calls:
        assert call["kwargs"].get("ordered") is False


@pytest.mark.asyncio
async def test_batch_inherit_acl_raises_after_failure(federation_sync_service: FederationSyncService, monkeypatch):
    """ACL inheritance failures should abort the sync instead of reporting false success."""
    federation_id = PydanticObjectId()
    server_id = PydanticObjectId()

    federation_acl_entries = [
        _make_acl_entry(PrincipalType.USER, "kent", RegistryResourceType.FEDERATION, federation_id, RoleBits.OWNER),
    ]

    def mock_find(query, **kwargs):
        if query.get("resourceType") == RegistryResourceType.FEDERATION:
            return _FakeQuery(federation_acl_entries)
        raise Exception("Database connection error")

    monkeypatch.setattr("registry.services.federation_sync_service.RegistryAclEntry.find", mock_find)

    with pytest.raises(RuntimeError, match="ACL inheritance failed"):
        await federation_sync_service._batch_inherit_federation_acl(
            federation_acl_entries=federation_acl_entries,
            resources=[(ResourceType.MCPSERVER, server_id)],
        )
