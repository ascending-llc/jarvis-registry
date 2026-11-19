"""Unit tests for RegistryContainer service wiring and lifecycle."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beanie import PydanticObjectId

from registry.container import RegistryContainer
from registry.services.group_directory_client import EntraIdGroupDirectoryClient
from registry_pkgs.models import ExtendedGroupSource

_AIP_ARN = "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/bedrock-governance-sonnet"
_FALLBACK_MODEL = "amazon.nova-2-lite-v1:0"


def _make_settings(*, aip_arn: str | None = None) -> MagicMock:
    settings = MagicMock()
    settings.aws_bedrock_sonnet_aip_arn = aip_arn
    settings.aws_workflow_llm_model = _FALLBACK_MODEL
    settings.workflow_llm_model_id = aip_arn or _FALLBACK_MODEL
    settings.aws_region = "us-east-1"
    settings.aws_access_key_id = None
    settings.aws_secret_access_key = None
    settings.aws_session_token = None
    settings.jwt_signing_config = MagicMock()
    settings.embedding_reindex_catch_up_min_delay_seconds = None
    return settings


def _make_container(settings: MagicMock) -> RegistryContainer:
    container = RegistryContainer(
        settings=settings,
        db_client=MagicMock(),
        redis_client=MagicMock(),
    )
    container.__dict__["a2a_httpx_client"] = MagicMock()
    container.__dict__["azure_model_credential"] = MagicMock(token_provider=object())
    return container


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("vector_initialized", [True, False])
async def test_startup_warms_services_and_starts_background_runners(
    monkeypatch: pytest.MonkeyPatch, vector_initialized: bool
) -> None:
    container = _make_container(_make_settings())
    old_role = ("old-role", 1)
    roles = {("mcpServer", 1): PydanticObjectId()}
    container.role_cache[old_role] = PydanticObjectId()
    role_cache = container.role_cache
    load_roles = AsyncMock(return_value=roles)
    gc_generations = AsyncMock()
    monkeypatch.setattr("registry.container.load_role_cache", load_roles)
    monkeypatch.setattr("registry.container.gc_stale_embedding_generations", gc_generations)
    vector_service = MagicMock(initialize=AsyncMock(), is_initialized=vector_initialized)
    mcp_service = MagicMock(initialize=AsyncMock())
    container.__dict__["vector_service"] = vector_service
    container.__dict__["mcp_service"] = mcp_service
    runners = {
        name: MagicMock(start=AsyncMock())
        for name in (
            "skill_sync_job_runner",
            "embedding_maintenance_watcher",
            "embedding_reindex_job_runner",
            "vector_sync_reconciler",
            "vector_orphan_sweeper",
            "lease_reaper",
        )
    }
    container.__dict__.update(runners)

    with (
        patch("registry.container.WorkflowRunner") as workflow_runner,
        patch("registry.container.MongoDB"),
        patch("registry.container.build_legacy_bedrock_model"),
    ):
        await container.startup()
        workflow_runner.assert_called_once()
        assert container.workflow_runner is workflow_runner.return_value

    load_roles.assert_awaited_once_with()
    assert container.role_cache is role_cache
    assert container.role_cache == roles
    vector_service.initialize.assert_awaited_once_with()
    mcp_service.initialize.assert_awaited_once_with()
    gc_generations.assert_awaited_once_with(container.db_client)
    for runner in runners.values():
        runner.start.assert_awaited_once_with()


def _stub_shutdown_dependencies(container: RegistryContainer, monkeypatch) -> None:
    container.__dict__["lease_reaper"] = MagicMock(shutdown=AsyncMock())
    container.__dict__["background_task_tracker"] = MagicMock(shutdown=AsyncMock(return_value=0))
    container.__dict__["embedding_reindex_job_runner"] = MagicMock(shutdown=AsyncMock())
    container.__dict__["embedding_maintenance_watcher"] = MagicMock(shutdown=AsyncMock(), start=AsyncMock())
    container.__dict__["mcp_proxy_client"] = MagicMock(aclose=AsyncMock())
    container.__dict__["a2a_httpx_client"] = MagicMock(aclose=AsyncMock())
    container.__dict__["a2a_client_registry"] = MagicMock(close=AsyncMock())
    container.__dict__["azure_model_credential"] = MagicMock(close=MagicMock())
    container.__dict__["cloud_identity_client"] = MagicMock(aclose=AsyncMock())


@pytest.mark.unit
class TestWorkflowRunnerModelSelection:
    @patch("registry.container.WorkflowRunner")
    @patch("registry.container.MongoDB")
    @patch("registry.container.build_legacy_bedrock_model")
    def test_uses_aip_arn_when_set(self, mock_model_factory, mock_mongodb, mock_runner):
        container = _make_container(_make_settings(aip_arn=_AIP_ARN))

        _ = container.workflow_runner

        mock_model_factory.assert_called_once_with(_AIP_ARN, "us-east-1")
        assert mock_runner.call_args.kwargs["fallback_model"] is mock_model_factory.return_value

    @patch("registry.container.WorkflowRunner")
    @patch("registry.container.MongoDB")
    @patch("registry.container.build_legacy_bedrock_model")
    def test_falls_back_to_workflow_llm_model_when_arn_not_set(self, mock_model_factory, mock_mongodb, mock_runner):
        container = _make_container(_make_settings(aip_arn=None))

        _ = container.workflow_runner

        mock_model_factory.assert_called_once_with(_FALLBACK_MODEL, "us-east-1")

    @patch("registry.container.WorkflowRunner")
    @patch("registry.container.MongoDB")
    @patch("registry.container.build_legacy_bedrock_model")
    def test_falls_back_to_workflow_llm_model_when_arn_empty_string(
        self,
        mock_model_factory,
        mock_mongodb,
        mock_runner,
    ):
        container = _make_container(_make_settings(aip_arn=""))

        _ = container.workflow_runner

        mock_model_factory.assert_called_once_with(_FALLBACK_MODEL, "us-east-1")


@pytest.mark.unit
@patch("registry.container.a2a_http.AsyncClient")
def test_a2a_httpx_client_pool_limits_come_from_settings(mock_async_client):
    """Long A2A reads hold pooled connections for the whole call, so the pool size must
    stay settings-tunable rather than hardcoded."""
    settings = _make_settings()
    settings.a2a_max_connections = 777  # sentinels, not the defaults, so pass-through is unambiguous
    settings.a2a_max_keepalive_connections = 13
    container = RegistryContainer(
        settings=settings,
        db_client=MagicMock(),
        redis_client=MagicMock(),
    )

    _ = container.a2a_httpx_client

    limits = mock_async_client.call_args.kwargs["limits"]
    assert limits.max_connections == 777
    assert limits.max_keepalive_connections == 13


@pytest.mark.unit
def test_proxy_and_azure_pools_use_their_own_settings():
    """The proxy pool is per target agent and the Azure pool is per federation, so they are
    sized independently. Distinct sentinels catch a cross-wired setting."""
    settings = _make_settings()
    settings.a2a_proxy_max_connections = 111
    settings.a2a_proxy_max_keepalive_connections = 11
    settings.azure_foundry_max_connections = 333
    settings.azure_foundry_max_keepalive_connections = 33
    container = RegistryContainer(
        settings=settings,
        db_client=MagicMock(),
        redis_client=MagicMock(),
    )

    registry = container.a2a_client_registry

    assert registry._agentcore_registry._limits.max_connections == 111
    assert registry._agentcore_registry._limits.max_keepalive_connections == 11
    assert registry._azure_client_cache._limits.max_connections == 333
    assert registry._azure_client_cache._limits.max_keepalive_connections == 33


@pytest.mark.unit
def test_skill_service_is_app_scoped_and_uses_shared_acl_service():
    container = _make_container(_make_settings())

    skill_service = container.skill_service

    assert skill_service is container.skill_service
    assert skill_service.acl_service is container.acl_service
    assert skill_service.user_service is container.user_service


@pytest.mark.asyncio
async def test_shutdown_delegates_to_idempotent_skill_sync_runner(monkeypatch):
    container = _make_container(_make_settings())
    _stub_shutdown_dependencies(container, monkeypatch)
    runner = MagicMock(shutdown=AsyncMock())
    container.__dict__["skill_sync_job_runner"] = runner

    await container.shutdown()

    runner.shutdown.assert_awaited_once()
    # The lease reaper is stopped and the background task tracker finalizes in-flight runs,
    # replacing the old cluster-wide cancel_in_flight_runs sweep.
    container.__dict__["lease_reaper"].shutdown.assert_awaited_once()
    container.__dict__["background_task_tracker"].shutdown.assert_awaited_once()


@pytest.mark.unit
def test_entra_slot_is_real_client_even_when_auth_provider_is_google():
    """The ENTRA slot is controlled only by entra_group_sync_enabled, not by AUTH_PROVIDER."""
    settings = _make_settings()
    settings.auth_provider = "google"
    settings.entra_group_sync_enabled = True
    settings.google_group_sync_enabled = False
    settings.entra_tenant_id = "tenant-id"
    settings.entra_client_id = "client-id"
    settings.entra_client_secret = "client-secret"
    settings.entra_graph_url = "https://graph.microsoft.com"
    container = _make_container(settings)

    clients = container.group_service._clients

    assert isinstance(clients[ExtendedGroupSource.ENTRA], EntraIdGroupDirectoryClient)


@pytest.mark.unit
def test_entra_slot_absent_and_client_never_built_when_sync_disabled():
    settings = _make_settings()
    settings.entra_group_sync_enabled = False
    settings.google_group_sync_enabled = False
    container = _make_container(settings)

    clients = container.group_service._clients

    assert ExtendedGroupSource.ENTRA not in clients
    assert "group_directory_client" not in container.__dict__


@pytest.mark.unit
def test_cloud_identity_client_is_scoped_to_google_customer_id():
    settings = _make_settings()
    settings.google_service_account_key_json = "{}"
    settings.google_customer_id = "C046psxkn"

    client = _make_container(settings).cloud_identity_client

    assert client._key_json == "{}"
    assert client._customer_id == "C046psxkn"
