from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock

import pytest
from beanie import PydanticObjectId

from registry.services.agentcore_import_service import AgentCoreImportService
from registry_pkgs.models import ExtendedMCPServer, ResourceType


class _FakeRepo:
    def __init__(self):
        self.synced = []

    async def sync_server_to_vector_db(self, server, is_delete=True):
        self.synced.append(server)
        return {"indexed_tools": 1, "failed_tools": 0, "deleted": 0}

    async def smart_sync(self, server):
        self.synced.append(server)
        return True

    async def delete_by_server_id(self, server_id, server_name=None):
        self.synced.append(("deleted", server_id, server_name))
        return True


class _FakeA2ARepo:
    def __init__(self):
        self.synced = []

    async def sync_agent_to_vector_db(self, agent, is_delete=True):
        self.synced.append((agent, is_delete))
        return {"indexed": 1, "failed": 0, "deleted": 0}

    async def delete_by_agent_id(self, agent_id, agent_name=None):
        self.synced.append(("deleted", agent_id, agent_name))
        return True


class _FakeAclService:
    def __init__(self):
        self.grants = []

    async def grant_permission(self, **kwargs):
        self.grants.append(kwargs)
        return SimpleNamespace(**kwargs)


class _FakeServer:
    def __init__(
        self,
        name: str,
        federation_id: str,
        title: str = "title",
    ):
        self.id = None
        self.serverName = name
        self.path = f"/agentcore/{name}"
        self.tags = ["agentcore"]
        self.config = {
            "title": title,
            "description": f"desc-{name}",
            "type": "streamable-http",
            "url": "https://example.com/mcp",
            "requiresOAuth": True,
            "authProvider": "bedrock-agentcore",
            "timeout": 30000,
            "initDuration": 60000,
        }
        self.status = "active"
        self.numTools = 0
        self.author = PydanticObjectId()
        self.federationMetadata = {
            "sourceType": "gateway_target",
            "runtimeArn": federation_id,
            "runtimeVersion": "1",
        }
        self.createdAt = datetime.now(UTC)
        self.updatedAt = datetime.now(UTC)
        self.save = AsyncMock()


@pytest.mark.unit
@pytest.mark.asyncio
class TestAgentCoreImportService:
    @pytest.fixture
    def repo(self):
        return _FakeRepo()

    @pytest.fixture
    def acl(self):
        return _FakeAclService()

    @pytest.fixture
    def a2a_repo(self):
        return _FakeA2ARepo()

    @pytest.fixture
    def service(self, repo, acl, a2a_repo):
        server_service = SimpleNamespace(create_server=AsyncMock())
        user_service = SimpleNamespace(get_user_by_user_id=AsyncMock())
        return AgentCoreImportService(
            federation_client=SimpleNamespace(),
            acl_service_instance=acl,
            server_service=server_service,
            user_service_instance=user_service,
            mcp_server_repo=repo,
            a2a_agent_repo=a2a_repo,
        )

    async def test_import_single_server_dry_run_created(self, service, monkeypatch):
        discovered = _FakeServer(name="srv-new", federation_id="fed-new")
        monkeypatch.setattr(ExtendedMCPServer, "find_one", AsyncMock(return_value=None))

        result = await service._import_single_server(
            discovered_server=discovered,
            owner_id=None,
            viewer_id=None,
            dry_run=True,
        )

        assert result["action"] == "would_create"
        assert result["server_name"] == "srv-new"

    async def test_import_single_server_updates_existing(self, service, repo, monkeypatch):
        existing = _FakeServer(name="srv-upd", federation_id="fed-upd", title="old-title")
        existing.id = PydanticObjectId()
        discovered = _FakeServer(name="srv-upd", federation_id="fed-upd", title="new-title")
        discovered.federationMetadata["runtimeVersion"] = "2"

        monkeypatch.setattr(ExtendedMCPServer, "find_one", AsyncMock(return_value=existing))

        result = await service._import_single_server(
            discovered_server=discovered,
            owner_id=PydanticObjectId(),
            viewer_id=None,
            dry_run=False,
        )

        assert result["action"] == "updated"
        assert result["changes"] == ["runtimeVersion: 1 -> 2"]
        existing.save.assert_awaited_once()
        assert len(repo.synced) == 1

    async def test_create_server_uses_server_service_create_server(self, service, repo, monkeypatch):
        discovered = _FakeServer(name="srv-create", federation_id="fed-create")
        owner_id = PydanticObjectId()
        viewer_id = PydanticObjectId()

        created_server = _FakeServer(name="created-from-service", federation_id="tmp")
        created_server.id = PydanticObjectId()
        created_server.save = AsyncMock()

        create_mock = AsyncMock(return_value=created_server)
        service.server_service = SimpleNamespace(create_server=create_mock)

        await service._create_server(
            discovered_server=discovered,
            owner_id=owner_id,
            viewer_id=viewer_id,
        )

        create_mock.assert_awaited_once()
        kwargs = create_mock.await_args.kwargs
        assert kwargs["skip_post_registration_checks"] is True
        assert kwargs["user_id"] == str(owner_id)
        assert kwargs["data"].title == discovered.config["title"]
        assert kwargs["data"].url == discovered.config["url"]
        assert kwargs["data"].authProvider == discovered.config["authProvider"]
        assert kwargs["data"].type == discovered.config["type"]
        assert kwargs["data"].requiresOauth is True
        assert kwargs["data"].initTimeout == discovered.config["initDuration"]
        assert created_server.federationMetadata == discovered.federationMetadata
        assert len(repo.synced) == 1

    async def test_import_from_runtime_continues_on_error(self, service, monkeypatch):
        discovered_1 = _FakeServer(name="srv-1", federation_id="fed-1")
        discovered_2 = _FakeServer(name="srv-2", federation_id="fed-2")

        discover_runtime_entities = AsyncMock(
            return_value={
                "mcp_servers": [discovered_1, discovered_2],
                "a2a_agents": [],
                "skipped_runtimes": [],
            }
        )
        service.federation_client = SimpleNamespace(discover_runtime_entities=discover_runtime_entities)

        monkeypatch.setattr(
            service,
            "_resolve_identities",
            self._async_return((PydanticObjectId(), None)),
        )
        monkeypatch.setattr(service, "_collect_stale_entities", self._async_return(([], [])))

        call_count = {"n": 0}

        async def _fake_import_single_server(**_kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return {
                    "action": "created",
                    "server_name": "srv-1",
                    "server_id": "x1",
                    "changes": ["new server"],
                    "error": None,
                }
            raise RuntimeError("boom")

        monkeypatch.setattr(service, "_import_single_server_in_transaction", _fake_import_single_server)

        result = await service.import_from_runtime(
            dry_run=False,
            user_id="dummy",
            aws_region="us-west-2",
            runtime_arn="arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test",
        )

        discover_runtime_entities.assert_awaited_once_with(
            runtime_arns=["arn:aws:bedrock-agentcore:us-west-2:123456789012:runtime/test"],
            author_id=ANY,
            region="us-west-2",
        )

        assert result["discovered"]["mcp_servers"] == 2
        assert result["runtime_filter_count"] == 1
        assert result["created"]["mcp_servers"] == 1
        assert result["skipped"]["mcp_servers"] == 1
        assert len(result["errors"]) == 1

    async def test_ensure_acl_permissions_grants_owner_and_viewer(self, service, acl):
        owner_id = PydanticObjectId()
        viewer_id = PydanticObjectId()

        await service._ensure_acl_permissions(
            resource_type=ResourceType.MCPSERVER,
            resource_id=PydanticObjectId(),
            owner_id=owner_id,
            viewer_id=viewer_id,
            dry_run=False,
        )

        assert len(acl.grants) == 2
        assert acl.grants[0]["perm_bits"] == 15  # OWNER
        assert acl.grants[1]["perm_bits"] == 1  # VIEW

    async def test_create_a2a_agent_syncs_to_vector_db(self, service, a2a_repo):
        owner_id = PydanticObjectId()
        discovered = SimpleNamespace(
            id=PydanticObjectId(),
            path="/agent/a2a",
            card=SimpleNamespace(name="a2a-new", model_dump=lambda **_: {"name": "a2a-new"}),
            tags=["agentcore"],
            status="active",
            isEnabled=True,
            wellKnown=None,
            federationMetadata={"sourceType": "runtime", "runtimeArn": "fed-a2a-new"},
            createdAt=datetime.now(UTC),
            updatedAt=datetime.now(UTC),
            author=None,
            insert=AsyncMock(),
        )

        created = await service._create_a2a_agent(
            discovered_agent=discovered,
            owner_id=owner_id,
            viewer_id=None,
        )

        assert created is discovered
        discovered.insert.assert_awaited_once()
        assert len(a2a_repo.synced) == 1
        assert a2a_repo.synced[0][0] is discovered
        assert a2a_repo.synced[0][1] is False

    async def test_update_a2a_agent_syncs_to_vector_db(self, service, a2a_repo):
        existing = SimpleNamespace(
            id=PydanticObjectId(),
            path="/agent/a2a",
            card=SimpleNamespace(name="a2a-upd", model_dump=lambda **_: {"name": "a2a-upd-old"}),
            tags=["old"],
            status="inactive",
            isEnabled=False,
            wellKnown=None,
            federationMetadata={"sourceType": "runtime", "runtimeArn": "fed-a2a-upd", "runtimeVersion": "1"},
            save=AsyncMock(),
        )
        new_data = SimpleNamespace(
            path="/agent/a2a",
            card=SimpleNamespace(name="a2a-upd", model_dump=lambda **_: {"name": "a2a-upd-new"}),
            tags=["new"],
            status="active",
            isEnabled=True,
            wellKnown=None,
            federationMetadata={"sourceType": "runtime", "runtimeArn": "fed-a2a-upd", "runtimeVersion": "2"},
        )

        changes = await service._update_a2a_agent(existing=existing, new_data=new_data)

        assert changes
        existing.save.assert_awaited_once()
        assert len(a2a_repo.synced) == 1
        assert a2a_repo.synced[0][0] is existing
        assert a2a_repo.synced[0][1] is True

    async def test_collects_stale_entities(self, service, monkeypatch):
        stale_server = _FakeServer(name="stale-mcp", federation_id="arn:runtime:stale")
        stale_server.id = PydanticObjectId()
        stale_server.federationMetadata = {"sourceType": "runtime", "runtimeArn": "arn:runtime:stale"}
        stale_agent = SimpleNamespace(
            id=PydanticObjectId(),
            card=SimpleNamespace(name="stale-a2a"),
            federationMetadata={"sourceType": "runtime", "runtimeArn": "arn:runtime:stale2"},
        )

        find_mock_mcp = SimpleNamespace(to_list=self._async_return([stale_server]))
        find_mock_a2a = SimpleNamespace(to_list=self._async_return([stale_agent]))
        monkeypatch.setattr(ExtendedMCPServer, "find", lambda *_args, **_kwargs: find_mock_mcp)
        monkeypatch.setattr("registry.services.agentcore_import_service.A2AAgent.find", lambda *_a, **_k: find_mock_a2a)

        stale_mcp, stale_a2a = await service._collect_stale_entities(
            all_discovered_runtime_arns=set(),
            discovered_mcp_ids=set(),
            discovered_a2a_ids=set(),
        )

        assert len(stale_mcp) == 1
        assert len(stale_a2a) == 1

    async def test_update_server_removes_old_requires_oauth_key(self, service, repo):
        existing = _FakeServer(name="srv-noise", federation_id="fed-noise", title="same-title")
        existing.id = PydanticObjectId()
        existing.config["requiresOauth"] = False
        existing.config.pop("requiresOAuth", None)

        discovered = _FakeServer(name="srv-noise", federation_id="fed-noise", title="same-title")
        discovered.config["requiresOAuth"] = False
        discovered.config.pop("requiresOauth", None)
        discovered.federationMetadata["runtimeVersion"] = "2"

        await service._update_server(existing=existing, new_data=discovered, changes=["config changed"])

        assert "requiresOauth" not in existing.config
        assert existing.config.get("requiresOAuth") is False
        assert len(repo.synced) == 1

    async def test_detect_changes_only_uses_runtime_version(self, service):
        existing = _FakeServer(name="srv-version", federation_id="fed-version")
        discovered_same = _FakeServer(name="srv-version", federation_id="fed-version")
        discovered_new = _FakeServer(name="srv-version", federation_id="fed-version")
        discovered_new.federationMetadata["runtimeVersion"] = "2"

        assert service._detect_changes(existing, discovered_same) == []
        assert service._detect_changes(existing, discovered_new) == ["runtimeVersion: 1 -> 2"]

    async def test_detect_a2a_changes_only_uses_runtime_version(self, service):
        existing = SimpleNamespace(federationMetadata={"sourceType": "runtime", "runtimeVersion": "3"})
        discovered_same = SimpleNamespace(federationMetadata={"sourceType": "runtime", "runtimeVersion": "3"})
        discovered_new = SimpleNamespace(federationMetadata={"sourceType": "runtime", "runtimeVersion": "4"})

        assert service._detect_a2a_changes(existing, discovered_same) == []
        assert service._detect_a2a_changes(existing, discovered_new) == ["runtimeVersion: 3 -> 4"]

    async def test_detect_changes_ignores_non_version_payload_drift(self, service):
        existing = _FakeServer(name="srv-no-drift", federation_id="fed-no-drift", title="old")
        discovered_same_version = _FakeServer(name="srv-no-drift", federation_id="fed-no-drift", title="new")
        discovered_same_version.config["description"] = "changed-desc"
        discovered_same_version.tags = ["changed-tag"]
        discovered_same_version.status = "inactive"
        discovered_same_version.federationMetadata["extra"] = {"changed": True}

        assert service._detect_changes(existing, discovered_same_version) == []

    async def test_detect_runtime_version_change_supports_int(self, service):
        existing = {"runtimeVersion": 2}
        new_data = {"runtimeVersion": 3}
        assert service.detect_runtime_version_change(existing, new_data) == ["runtimeVersion: 2 -> 3"]

    async def test_detect_runtime_version_change_handles_missing_version(self, service):
        assert service.detect_runtime_version_change({"sourceType": "runtime"}, {"sourceType": "runtime"}) == []
        assert service.detect_runtime_version_change({"sourceType": "runtime"}, {"runtimeVersion": "1"}) == [
            "runtimeVersion: None -> 1"
        ]

    @staticmethod
    def _async_return(value):
        async def _inner(*_args, **_kwargs):
            return value

        return _inner
