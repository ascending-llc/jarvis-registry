"""Tests for federation sync orchestration and lifecycle operations."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId

from registry.services.federation.federation_handlers import AwsAgentCoreSyncHandler, AzureAiFoundrySyncHandler
from registry.services.federation_sync_service import (
    FederationSyncMutationResult,
    FederationSyncService,
    VectorSyncOutcome,
    run_federation_sync_background,
)
from registry_pkgs.models.enums import (
    FederationProviderType,
    FederationStatus,
    FederationSyncStatus,
)
from registry_pkgs.models.federation_sync_job import FederationApplySummary
from tests.unit.services.federation_sync_test_helpers import (
    _DEFAULT_USER_OBJECT_ID,
    _make_federation,
    _patch_mongo_session,
)

pytestmark = pytest.mark.usefixtures("default_empty_access_roles")


@pytest.mark.asyncio
async def test_run_federation_sync_background_swallows_execution_error():
    federation = SimpleNamespace(id=PydanticObjectId())
    job = SimpleNamespace(id=PydanticObjectId())
    service = MagicMock()
    service.run_sync = AsyncMock(side_effect=RuntimeError("discovery failed"))

    await run_federation_sync_background(
        federation_sync_service=service,
        federation=federation,
        job=job,
        author_id=_DEFAULT_USER_OBJECT_ID,
    )

    service.run_sync.assert_awaited_once_with(
        federation=federation,
        job=job,
        author_id=_DEFAULT_USER_OBJECT_ID,
    )


@pytest.mark.asyncio
async def test_discover_entities_dispatches_to_aws_handler(federation_sync_service: FederationSyncService):
    federation = _make_federation(
        FederationProviderType.AWS_AGENTCORE,
        {"region": "us-east-1", "assumeRoleArn": "arn:aws:iam::123456789012:role/TestRole"},
    )
    expected = {"mcp_servers": [], "a2a_agents": [], "skipped_runtimes": []}

    aws_handler = MagicMock(spec=AwsAgentCoreSyncHandler)
    aws_handler.discover_entities = AsyncMock(return_value=expected)
    federation_sync_service.sync_handlers[FederationProviderType.AWS_AGENTCORE] = aws_handler

    result = await federation_sync_service._discover_entities(federation, author_id=_DEFAULT_USER_OBJECT_ID)

    aws_handler.discover_entities.assert_awaited_once_with(federation, author_id=_DEFAULT_USER_OBJECT_ID)
    assert result == expected


@pytest.mark.asyncio
async def test_aws_handler_passes_resource_tags_filter_to_client():
    fake_discovery_client = MagicMock()
    fake_runtime_invoker = MagicMock()
    fake_runtime_invoker.enrich_mcp_server = AsyncMock()
    fake_runtime_invoker.enrich_a2a_agent = AsyncMock()
    handler = AwsAgentCoreSyncHandler(
        discovery_client=fake_discovery_client,
        runtime_invoker=fake_runtime_invoker,
    )
    federation = _make_federation(
        FederationProviderType.AWS_AGENTCORE,
        {
            "region": "us-east-1",
            "assumeRoleArn": "arn:aws:iam::123456789012:role/TestRole",
            "resourceTagsFilter": {"env": "production", "team": "platform"},
        },
    )
    fake_discovery_client.discover_runtime_entities = AsyncMock(
        return_value={"mcp_servers": [], "a2a_agents": [], "skipped_runtimes": []}
    )

    result = await handler.discover_entities(federation, author_id=_DEFAULT_USER_OBJECT_ID)

    fake_discovery_client.discover_runtime_entities.assert_awaited_once_with(
        author_id=_DEFAULT_USER_OBJECT_ID,
        region="us-east-1",
        assume_role_arn="arn:aws:iam::123456789012:role/TestRole",
        resource_tags_filter={"env": "production", "team": "platform"},
    )
    assert result == {"mcp_servers": [], "a2a_agents": [], "skipped_runtimes": []}


@pytest.mark.asyncio
async def test_azure_sync_dispatches_through_handler(federation_sync_service: FederationSyncService):
    """Sync dispatches to AzureAiFoundrySyncHandler. Credential resolution (managed
    identity vs. service principal) is covered in test_azure_foundry_auth.py."""
    federation = _make_federation(
        FederationProviderType.AZURE_AI_FOUNDRY,
        {"projectEndpoint": "https://example.projects.ai.azure.com"},
    )
    expected = {"mcp_servers": [], "a2a_agents": []}

    azure_handler = MagicMock(spec=AzureAiFoundrySyncHandler)
    azure_handler.discover_entities = AsyncMock(return_value=expected)
    federation_sync_service.sync_handlers[FederationProviderType.AZURE_AI_FOUNDRY] = azure_handler

    result = await federation_sync_service._discover_entities(federation, author_id=_DEFAULT_USER_OBJECT_ID)

    azure_handler.discover_entities.assert_awaited_once_with(federation, author_id=_DEFAULT_USER_OBJECT_ID)
    assert result == expected


@pytest.mark.asyncio
async def test_azure_handler_discover_entities_uses_shared_cache_auth_service():
    """AzureAiFoundrySyncHandler resolves its auth through the shared cache (not a per-call build)
    and hands that credential to the discovery client."""
    federation = _make_federation(
        FederationProviderType.AZURE_AI_FOUNDRY,
        {"projectEndpoint": "https://example.projects.ai.azure.com"},
    )
    auth = object()
    cache = MagicMock()
    cache.get_auth_service = AsyncMock(return_value=auth)
    discovery_client = MagicMock()
    discovery_client.discover_a2a_agents = AsyncMock(return_value=["agent"])
    handler = AzureAiFoundrySyncHandler(azure_client_cache=cache, discovery_client=discovery_client)

    result = await handler.discover_entities(federation, author_id=_DEFAULT_USER_OBJECT_ID)

    cache.get_auth_service.assert_awaited_once_with(federation.id)
    assert discovery_client.discover_a2a_agents.await_args.kwargs["auth"] is auth
    assert discovery_client.discover_a2a_agents.await_args.kwargs["author_id"] == _DEFAULT_USER_OBJECT_ID
    assert result == {"a2a_agents": ["agent"], "mcp_servers": []}


@pytest.mark.asyncio
async def test_run_delete_marks_job_success_and_cleans_up_vectors(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(
        FederationProviderType.AWS_AGENTCORE,
        {"region": "us-east-1", "assumeRoleArn": "arn:aws:iam::123456789012:role/TestRole"},
    )
    federation.status = FederationStatus.DELETING
    job = SimpleNamespace(id=PydanticObjectId(), jobType="delete_sync", startedAt=datetime.now(UTC))

    mcp_arns = ["arn:aws:bedrock-agentcore:us-east-1:123:runtime/mcp-1"]
    a2a_arns = ["arn:aws:bedrock-agentcore:us-east-1:123:runtime/a2a-1"]

    federation_sync_service.federation_job_service.mark_syncing = AsyncMock()
    federation_sync_service.federation_job_service.mark_success = AsyncMock()
    federation_sync_service._delete_transaction = AsyncMock(return_value=(mcp_arns, a2a_arns))
    federation_sync_service._delete_vectors_for_federation = AsyncMock(return_value=[])
    mongo_session = _patch_mongo_session(monkeypatch)

    result = await federation_sync_service.run_delete(federation=federation, job=job)

    federation_sync_service._delete_transaction.assert_awaited_once_with(
        federation,
        current_job_id=job.id,
        session=mongo_session,
    )
    federation_sync_service._delete_vectors_for_federation.assert_awaited_once_with(
        str(federation.id), mcp_arns, a2a_arns
    )
    federation_sync_service.federation_job_service.mark_success.assert_awaited_once_with(job)
    assert result is job


@pytest.mark.asyncio
async def test_run_delete_records_vector_errors_in_job_but_still_succeeds(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(
        FederationProviderType.AWS_AGENTCORE,
        {"region": "us-east-1", "assumeRoleArn": "arn:aws:iam::123456789012:role/TestRole"},
    )
    federation.status = FederationStatus.DELETING
    job = SimpleNamespace(
        id=PydanticObjectId(),
        jobType="delete_sync",
        startedAt=datetime.now(UTC),
        applySummary=FederationApplySummary(),
    )

    vector_errors = ["mcp vector cleanup failed for arn:aws:bedrock-agentcore:us-east-1:123:runtime/mcp-1"]

    federation_sync_service.federation_job_service.mark_syncing = AsyncMock()
    federation_sync_service.federation_job_service.mark_success = AsyncMock()
    federation_sync_service._delete_transaction = AsyncMock(return_value=(["arn:..."], []))
    federation_sync_service._delete_vectors_for_federation = AsyncMock(return_value=vector_errors)
    _patch_mongo_session(monkeypatch)

    result = await federation_sync_service.run_delete(federation=federation, job=job)

    assert result.applySummary.errorMessages == vector_errors
    federation_sync_service.federation_job_service.mark_success.assert_awaited_once_with(job)


@pytest.mark.asyncio
async def test_vector_cleanup_completes_mcp_before_a2a_collection_preflight(
    federation_sync_service: FederationSyncService,
):
    mcp_runtime_arn = "arn:mcp:healthy"
    a2a_runtime_arn = "arn:a2a:unavailable"
    events: list[str] = []

    async def _ensure_mcp_collection() -> None:
        events.append("mcp-preflight")

    async def _delete_mcp(_federation_id: str, _runtime_arn: str) -> None:
        events.append("mcp-delete")

    async def _ensure_a2a_collection() -> None:
        events.append("a2a-preflight")
        raise RuntimeError("a2a collection unavailable")

    federation_sync_service.mcp_server_repo.ensure_collection = AsyncMock(side_effect=_ensure_mcp_collection)
    federation_sync_service.mcp_server_repo.delete_by_runtime_identity = AsyncMock(side_effect=_delete_mcp)
    federation_sync_service.a2a_agent_repo.ensure_collection = AsyncMock(side_effect=_ensure_a2a_collection)
    federation_sync_service.a2a_agent_repo.delete_by_runtime_identity = AsyncMock()

    with pytest.raises(RuntimeError, match="a2a collection unavailable"):
        await federation_sync_service._delete_vectors_for_federation(
            "federation-1",
            [mcp_runtime_arn],
            [a2a_runtime_arn],
        )

    federation_sync_service.mcp_server_repo.delete_by_runtime_identity.assert_awaited_once_with(
        "federation-1",
        mcp_runtime_arn,
    )
    federation_sync_service.a2a_agent_repo.delete_by_runtime_identity.assert_not_awaited()
    assert events == ["mcp-preflight", "mcp-delete", "a2a-preflight"]


@pytest.mark.asyncio
async def test_vector_cleanup_isolates_per_runtime_delete_failure(
    federation_sync_service: FederationSyncService,
):
    attempted: list[str] = []

    async def _delete(_federation_id: str, runtime_arn: str) -> None:
        attempted.append(runtime_arn)
        if runtime_arn == "arn:mcp:broken":
            raise RuntimeError("delete failed")

    federation_sync_service.mcp_server_repo.delete_by_runtime_identity = AsyncMock(side_effect=_delete)

    errors = await federation_sync_service._delete_vectors_for_federation(
        "federation-1",
        ["arn:mcp:broken", "arn:mcp:healthy"],
        [],
    )

    assert attempted == ["arn:mcp:broken", "arn:mcp:healthy"]
    assert errors == ["mcp vector cleanup failed for arn:mcp:broken"]


@pytest.mark.asyncio
async def test_run_delete_restores_active_status_when_delete_fails(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(
        FederationProviderType.AWS_AGENTCORE,
        {"region": "us-east-1", "assumeRoleArn": "arn:aws:iam::123456789012:role/TestRole"},
    )
    federation.status = FederationStatus.DELETING
    federation.syncStatus = FederationSyncStatus.SYNCING
    job = SimpleNamespace(id=PydanticObjectId(), jobType="delete_sync", startedAt=datetime.now(UTC))

    federation_sync_service.federation_job_service.mark_syncing = AsyncMock()
    federation_sync_service.federation_job_service.mark_failed = AsyncMock()
    federation_sync_service.federation_crud_service.mark_delete_failed = AsyncMock()
    federation_sync_service._delete_transaction = AsyncMock(side_effect=RuntimeError("delete failed"))
    _patch_mongo_session(monkeypatch)

    with pytest.raises(RuntimeError, match="delete failed"):
        await federation_sync_service.run_delete(federation=federation, job=job)

    federation_sync_service.federation_crud_service.mark_delete_failed.assert_awaited_once_with(
        federation, "delete failed"
    )
    federation_sync_service.federation_job_service.mark_failed.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_federation_and_create_resync_job_creates_pending_job(
    federation_sync_service: FederationSyncService,
):
    federation = _make_federation(
        FederationProviderType.AWS_AGENTCORE,
        {"region": "us-east-1", "assumeRoleArn": "arn:aws:iam::123456789012:role/TestRole"},
    )
    updated = SimpleNamespace(
        **{
            **federation.__dict__,
            "providerConfig": {"region": "us-west-2", "assumeRoleArn": "arn:aws:iam::123456789012:role/TestRole"},
        }
    )
    job = SimpleNamespace(id=PydanticObjectId(), jobType="config_resync", createdAt=datetime.now(UTC))

    federation_sync_service.federation_crud_service.update_federation = AsyncMock(return_value=updated)
    federation_sync_service.federation_job_service.create_job = AsyncMock(return_value=job)
    federation_sync_service.federation_crud_service.mark_sync_pending = AsyncMock(return_value=updated)
    session = object()

    result, created_job = await federation_sync_service.update_federation_and_create_resync_job(
        federation=federation,
        display_name="Updated",
        description="Updated",
        tags=["prod"],
        normalized_provider_config={"region": "us-west-2", "assumeRoleArn": "arn:aws:iam::123456789012:role/TestRole"},
        updated_by="user-1",
        session=session,
    )

    federation_sync_service.federation_crud_service.update_federation.assert_awaited_once()
    federation_sync_service.federation_job_service.create_job.assert_awaited_once()
    federation_sync_service.federation_crud_service.mark_sync_pending.assert_awaited_once()
    assert federation_sync_service.federation_crud_service.update_federation.await_args.kwargs["session"] is session
    assert federation_sync_service.federation_job_service.create_job.await_args.kwargs["session"] is session
    assert federation_sync_service.federation_crud_service.mark_sync_pending.await_args.kwargs["session"] is session
    assert federation_sync_service.federation_crud_service.mark_sync_pending.await_args.args[0] == updated
    assert federation_sync_service.federation_crud_service.mark_sync_pending.await_args.kwargs["last_sync"].status == (
        FederationSyncStatus.PENDING
    )
    assert result == updated
    assert created_job == job


@pytest.mark.asyncio
async def test_run_sync_calls_vector_sync_after_commit(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    job = SimpleNamespace(
        id=PydanticObjectId(),
        jobType="full_sync",
        startedAt=datetime.now(UTC),
        discoverySummary=SimpleNamespace(discoveredMcpServers=0, discoveredAgents=0),
    )
    mutation_result = FederationSyncMutationResult(
        summary=FederationApplySummary(),
    )

    federation_sync_service._discover_entities = AsyncMock(return_value={"mcp_servers": [], "a2a_agents": []})
    federation_sync_service._commit_bookkeeping_transaction = AsyncMock(
        return_value=SimpleNamespace(summary=mutation_result.summary)
    )
    federation_sync_service._apply_sync_plan = AsyncMock(return_value=mutation_result)
    federation_sync_service.federation_job_service.update_apply_summary = AsyncMock()
    federation_sync_service._sync_vector_index_after_commit = AsyncMock(return_value=VectorSyncOutcome())
    federation_sync_service._finalize_sync_status = AsyncMock()
    federation_sync_service.federation_job_service.mark_syncing = AsyncMock()
    mongo_session = _patch_mongo_session(monkeypatch)

    result = await federation_sync_service.run_sync(federation=federation, job=job, author_id=_DEFAULT_USER_OBJECT_ID)

    assert result == job
    federation_sync_service._commit_bookkeeping_transaction.assert_awaited_once_with(
        federation=federation,
        job=job,
        discovered={"mcp_servers": [], "a2a_agents": []},
        session=mongo_session,
    )
    federation_sync_service._apply_sync_plan.assert_awaited_once()
    federation_sync_service._sync_vector_index_after_commit.assert_awaited_once_with(
        federation=federation,
        job=job,
        mutation_result=mutation_result,
    )
    federation_sync_service._finalize_sync_status.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_sync_passes_vector_outcome_to_finalize(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    job = SimpleNamespace(
        id=PydanticObjectId(),
        jobType="full_sync",
        createdAt=datetime.now(UTC),
        startedAt=datetime.now(UTC),
        discoverySummary=SimpleNamespace(discoveredMcpServers=1, discoveredAgents=0),
    )
    mutation_result = FederationSyncMutationResult(
        summary=FederationApplySummary(),
    )
    vector_outcome = VectorSyncOutcome(
        failed_changed_mcp_runtime_arns={"arn:mcp:1"},
        error_messages=["mcp runtime rebuild failed:fed:arn:mcp:1:vector down"],
    )

    federation_sync_service._discover_entities = AsyncMock(return_value={"mcp_servers": [], "a2a_agents": []})
    federation_sync_service._commit_bookkeeping_transaction = AsyncMock(
        return_value=SimpleNamespace(summary=mutation_result.summary)
    )
    federation_sync_service._apply_sync_plan = AsyncMock(return_value=mutation_result)
    federation_sync_service.federation_job_service.update_apply_summary = AsyncMock()
    federation_sync_service._sync_vector_index_after_commit = AsyncMock(return_value=vector_outcome)
    federation_sync_service._finalize_sync_status = AsyncMock()
    federation_sync_service.federation_job_service.mark_syncing = AsyncMock()
    _patch_mongo_session(monkeypatch)

    result = await federation_sync_service.run_sync(federation=federation, job=job, author_id=_DEFAULT_USER_OBJECT_ID)

    assert result == job
    federation_sync_service._finalize_sync_status.assert_awaited_once_with(
        federation, job, mutation_result, vector_outcome
    )


@pytest.mark.asyncio
async def test_run_sync_finalizes_committed_apply_when_vector_sync_setup_fails(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    job = SimpleNamespace(
        id=PydanticObjectId(),
        jobType="full_sync",
        createdAt=datetime.now(UTC),
        startedAt=datetime.now(UTC),
        discoverySummary=SimpleNamespace(discoveredMcpServers=1, discoveredAgents=1),
    )
    mutation_result = FederationSyncMutationResult(
        summary=FederationApplySummary(createdMcpServers=1, createdAgents=1),
        changed_mcp_runtime_arns={"arn:mcp:1"},
        changed_a2a_runtime_arns={"arn:a2a:1"},
    )

    federation_sync_service._discover_entities = AsyncMock(return_value={"mcp_servers": [], "a2a_agents": []})
    federation_sync_service._commit_bookkeeping_transaction = AsyncMock(
        return_value=SimpleNamespace(summary=mutation_result.summary)
    )
    federation_sync_service._apply_sync_plan = AsyncMock(return_value=mutation_result)
    federation_sync_service.federation_job_service.update_apply_summary = AsyncMock()
    federation_sync_service._sync_vector_index_after_commit = AsyncMock(
        side_effect=RuntimeError("Mongo runtime ARN read failed")
    )
    federation_sync_service._finalize_sync_status = AsyncMock()
    federation_sync_service.federation_job_service.mark_syncing = AsyncMock()
    federation_sync_service.federation_job_service.mark_failed = AsyncMock()
    federation_sync_service.federation_crud_service.mark_sync_failed = AsyncMock()
    _patch_mongo_session(monkeypatch)

    result = await federation_sync_service.run_sync(
        federation=federation,
        job=job,
        author_id=_DEFAULT_USER_OBJECT_ID,
    )

    assert result == job
    federation_sync_service.federation_crud_service.mark_sync_failed.assert_not_awaited()
    federation_sync_service.federation_job_service.mark_failed.assert_not_awaited()
    federation_sync_service._finalize_sync_status.assert_awaited_once()
    vector_outcome = federation_sync_service._finalize_sync_status.await_args.args[3]
    assert vector_outcome.failed_changed_mcp_runtime_arns == {"arn:mcp:1"}
    assert vector_outcome.failed_changed_a2a_runtime_arns == {"arn:a2a:1"}
    assert vector_outcome.error_messages == [
        f"vector sync failed after Mongo commit:{federation.id}:Mongo runtime ARN read failed"
    ]


@pytest.mark.asyncio
async def test_preview_manual_sync_does_not_mutate_or_create_jobs(federation_sync_service: FederationSyncService):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    summary = FederationApplySummary(createdMcpServers=1)
    sync_plan = SimpleNamespace(summary=summary, discovered_mcp_count=1, discovered_a2a_count=0)

    discovered = {
        "mcp_servers": [SimpleNamespace()],
        "a2a_agents": [],
        "skipped_runtimes": [
            {"runtimeArn": "arn:transient", "reason": "tag_fetch_failed"},
            {"runtimeArn": "arn:filtered", "reason": "tag_filter_mismatch"},
        ],
    }
    federation_sync_service._discover_entities = AsyncMock(return_value=discovered)
    federation_sync_service._build_sync_plan = AsyncMock(return_value=sync_plan)
    federation_sync_service.federation_job_service.create_job = AsyncMock()
    federation_sync_service.federation_crud_service.mark_sync_pending = AsyncMock()
    federation_sync_service.federation_crud_service.mark_syncing = AsyncMock()
    federation_sync_service._sync_vector_index_after_commit = AsyncMock()

    result = await federation_sync_service.preview_manual_sync(
        federation=federation,
        reason="test",
        triggered_by="user-1",
    )

    assert result.provider_type == federation.providerType
    assert result.discovered_mcp_count == 1
    assert result.summary.createdMcpServers == 1
    federation_sync_service.federation_job_service.create_job.assert_not_awaited()
    federation_sync_service.federation_crud_service.mark_sync_pending.assert_not_awaited()
    federation_sync_service.federation_crud_service.mark_syncing.assert_not_awaited()
    federation_sync_service._sync_vector_index_after_commit.assert_not_awaited()
    assert federation_sync_service._build_sync_plan.await_args.kwargs["protected_runtime_arns"] == {"arn:transient"}


@pytest.mark.asyncio
async def test_create_manual_sync_job_returns_pending_job_without_running_sync(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    job = SimpleNamespace(id=PydanticObjectId())
    federation_sync_service.federation_job_service.get_active_job = AsyncMock(return_value=None)
    federation_sync_service.create_sync_job_and_mark_pending = AsyncMock(return_value=job)
    federation_sync_service.run_sync = AsyncMock()
    session = _patch_mongo_session(monkeypatch)

    result, author_id = await federation_sync_service.create_manual_sync_job(
        federation=federation,
        reason="manual",
        triggered_by="user-1",
    )

    assert result is job
    assert author_id == _DEFAULT_USER_OBJECT_ID
    federation_sync_service.create_sync_job_and_mark_pending.assert_awaited_once()
    assert federation_sync_service.create_sync_job_and_mark_pending.await_args.kwargs["session"] is session
    federation_sync_service.run_sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_manual_sync_raises_when_user_id_is_missing(federation_sync_service: FederationSyncService):
    # Author resolution happens before any job is created, so a missing user_id
    # must fail fast without touching job/federation state.
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    federation_sync_service.federation_job_service.get_active_job = AsyncMock()
    federation_sync_service.create_sync_job_and_mark_pending = AsyncMock()
    federation_sync_service.run_sync = AsyncMock()
    federation_sync_service.federation_crud_service.mark_sync_failed = AsyncMock()
    federation_sync_service.federation_job_service.mark_failed = AsyncMock()

    with pytest.raises(ValueError, match="requires a user_id"):
        await federation_sync_service.start_manual_sync(federation=federation, reason="test", triggered_by=None)

    federation_sync_service.federation_job_service.get_active_job.assert_not_awaited()
    federation_sync_service.create_sync_job_and_mark_pending.assert_not_awaited()
    federation_sync_service.run_sync.assert_not_awaited()
    federation_sync_service.federation_crud_service.mark_sync_failed.assert_not_awaited()
    federation_sync_service.federation_job_service.mark_failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_manual_sync_raises_when_user_not_found(federation_sync_service: FederationSyncService):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    federation_sync_service.user_service.get_user_by_user_id = AsyncMock(return_value=None)
    federation_sync_service.federation_job_service.get_active_job = AsyncMock()
    federation_sync_service.create_sync_job_and_mark_pending = AsyncMock()
    federation_sync_service.run_sync = AsyncMock()
    federation_sync_service.federation_crud_service.mark_sync_failed = AsyncMock()
    federation_sync_service.federation_job_service.mark_failed = AsyncMock()

    with pytest.raises(ValueError, match="user not found"):
        await federation_sync_service.start_manual_sync(federation=federation, reason="test", triggered_by="ghost-user")

    federation_sync_service.federation_job_service.get_active_job.assert_not_awaited()
    federation_sync_service.create_sync_job_and_mark_pending.assert_not_awaited()
    federation_sync_service.run_sync.assert_not_awaited()
    federation_sync_service.federation_crud_service.mark_sync_failed.assert_not_awaited()
    federation_sync_service.federation_job_service.mark_failed.assert_not_awaited()


def _arrange_resync_update(federation_sync_service: FederationSyncService, federation):
    federation_sync_service.federation_crud_service.validate_provider_config = MagicMock(
        return_value={"region": "us-west-2"}
    )
    federation_sync_service.federation_job_service.get_active_job = AsyncMock()
    federation_sync_service.update_federation_and_create_resync_job = AsyncMock()
    federation_sync_service.run_sync = AsyncMock()


async def _call_update_resync(federation_sync_service: FederationSyncService, federation, updated_by):
    return await federation_sync_service.update_federation_with_optional_resync(
        federation=federation,
        display_name="name",
        description=None,
        tags=[],
        provider_config={"region": "us-west-2"},
        updated_by=updated_by,
        sync_after_update=True,
    )


@pytest.mark.asyncio
async def test_update_with_resync_raises_when_user_id_is_missing(federation_sync_service: FederationSyncService):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    _arrange_resync_update(federation_sync_service, federation)

    with pytest.raises(ValueError, match="requires a user_id"):
        await _call_update_resync(federation_sync_service, federation, updated_by=None)

    federation_sync_service.federation_job_service.get_active_job.assert_not_awaited()
    federation_sync_service.update_federation_and_create_resync_job.assert_not_awaited()
    federation_sync_service.run_sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_with_resync_raises_when_user_not_found(federation_sync_service: FederationSyncService):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    _arrange_resync_update(federation_sync_service, federation)
    federation_sync_service.user_service.get_user_by_user_id = AsyncMock(return_value=None)

    with pytest.raises(ValueError, match="user not found"):
        await _call_update_resync(federation_sync_service, federation, updated_by="ghost-user")

    federation_sync_service.federation_job_service.get_active_job.assert_not_awaited()
    federation_sync_service.update_federation_and_create_resync_job.assert_not_awaited()
    federation_sync_service.run_sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_plain_update_skips_author_resolution(federation_sync_service: FederationSyncService):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    federation_sync_service.federation_crud_service.validate_provider_config = MagicMock(
        return_value={"region": "us-east-1"}
    )
    federation_sync_service.user_service.get_user_by_user_id = AsyncMock(return_value=None)
    federation_sync_service.federation_crud_service.update_federation = AsyncMock(return_value=federation)
    federation_sync_service.run_sync = AsyncMock()

    updated, job = await federation_sync_service.update_federation_with_optional_resync(
        federation=federation,
        display_name="name",
        description=None,
        tags=[],
        provider_config={"region": "us-east-1"},
        updated_by=None,
        sync_after_update=True,
    )

    assert updated is federation
    assert job is None
    federation_sync_service.user_service.get_user_by_user_id.assert_not_awaited()
    federation_sync_service.run_sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_preview_manual_sync_raises_when_user_id_is_missing(
    federation_sync_service: FederationSyncService,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    with pytest.raises(ValueError, match="requires a user_id"):
        await federation_sync_service.preview_manual_sync(
            federation=federation,
            reason="test",
            triggered_by=None,
        )


@pytest.mark.asyncio
async def test_run_sync_forwards_author_id_to_discover_entities(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    job = SimpleNamespace(
        id=PydanticObjectId(),
        startedAt=datetime.now(UTC),
        discoverySummary=SimpleNamespace(discoveredMcpServers=0, discoveredAgents=0),
    )
    mutation_result = FederationSyncMutationResult(
        summary=FederationApplySummary(),
    )

    discover_mock = AsyncMock(return_value={"mcp_servers": [], "a2a_agents": []})
    federation_sync_service._discover_entities = discover_mock
    federation_sync_service._commit_bookkeeping_transaction = AsyncMock(
        return_value=SimpleNamespace(summary=mutation_result.summary)
    )
    federation_sync_service._apply_sync_plan = AsyncMock(return_value=mutation_result)
    federation_sync_service.federation_job_service.update_apply_summary = AsyncMock()
    federation_sync_service._sync_vector_index_after_commit = AsyncMock(return_value=VectorSyncOutcome())
    federation_sync_service._finalize_sync_status = AsyncMock()
    federation_sync_service.federation_job_service.mark_syncing = AsyncMock()
    _patch_mongo_session(monkeypatch)

    await federation_sync_service.run_sync(federation=federation, job=job, author_id=_DEFAULT_USER_OBJECT_ID)

    discover_mock.assert_awaited_once_with(federation, author_id=_DEFAULT_USER_OBJECT_ID)


# ---------------------------------------------------------------------------
# T1 – single MCP create failure, others persist
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_apply_sync_plan_finishes_deletes_before_starting_creates(
    federation_sync_service: FederationSyncService,
):
    from registry.services.federation_sync_service import FederationSyncPlan

    events: list[str] = []

    async def _delete() -> None:
        events.append("delete-start")
        events.append("delete-finished")

    async def _insert() -> None:
        assert events == ["delete-start", "delete-finished"]
        events.append("create-start")
        created.id = PydanticObjectId()
        events.append("create-finished")

    async def _save() -> None:
        assert events == ["delete-start", "delete-finished", "create-start", "create-finished"]
        events.append("update-start")

    stale = SimpleNamespace(id=PydanticObjectId(), delete=AsyncMock(side_effect=_delete))
    created = SimpleNamespace(id=None, insert=AsyncMock(side_effect=_insert))
    existing = SimpleNamespace(
        id=PydanticObjectId(),
        serverName="old",
        path="/old",
        tags=[],
        config={},
        numTools=0,
        federationMetadata=None,
        save=AsyncMock(side_effect=_save),
    )
    discovered = SimpleNamespace(
        serverName="updated",
        path="/updated",
        tags=[],
        config={},
        numTools=1,
        federationMetadata=None,
    )
    sync_plan = FederationSyncPlan(
        summary=FederationApplySummary(deletedMcpServers=1, createdMcpServers=1),
        federation_id=PydanticObjectId(),
        provider_type=FederationProviderType.AWS_AGENTCORE,
        discovered_mcp_count=1,
        discovered_a2a_count=0,
        mcp_deletes=[(stale, "arn:old")],
        mcp_creates=[(created, "arn:new")],
        mcp_updates=[(existing, discovered, "arn:updated")],
    )
    federation_sync_service._get_federation_acl_entries = AsyncMock(return_value=([], True))

    await federation_sync_service._apply_sync_plan(sync_plan)

    assert events == ["delete-start", "delete-finished", "create-start", "create-finished", "update-start"]


@pytest.mark.asyncio
async def test_single_mcp_create_failure_others_persist(
    federation_sync_service: FederationSyncService,
):
    federation_id = PydanticObjectId()
    ok1 = SimpleNamespace(id=None, insert=AsyncMock(side_effect=lambda **kw: setattr(ok1, "id", PydanticObjectId())))
    bad = SimpleNamespace(id=None, insert=AsyncMock(side_effect=Exception("write conflict")))
    ok3 = SimpleNamespace(id=None, insert=AsyncMock(side_effect=lambda **kw: setattr(ok3, "id", PydanticObjectId())))

    from registry.services.federation_sync_service import FederationSyncPlan

    sync_plan = FederationSyncPlan(
        summary=FederationApplySummary(createdMcpServers=3),
        federation_id=federation_id,
        provider_type=FederationProviderType.AWS_AGENTCORE,
        discovered_mcp_count=3,
        discovered_a2a_count=0,
        mcp_creates=[(ok1, "arn:ok1"), (bad, "arn:bad"), (ok3, "arn:ok3")],
    )
    federation_sync_service._get_federation_acl_entries = AsyncMock(return_value=([], True))

    result = await federation_sync_service._apply_sync_plan(sync_plan)

    ok1.insert.assert_awaited_once()
    bad.insert.assert_awaited_once()
    ok3.insert.assert_awaited_once()
    assert result.summary.mongoApplyFailedMcpServers == 1
    assert "arn:ok1" in result.changed_mcp_runtime_arns
    assert "arn:ok3" in result.changed_mcp_runtime_arns
    assert "arn:bad" not in result.changed_mcp_runtime_arns
    assert any("arn:bad" in msg for msg in result.summary.errorMessages)


# ---------------------------------------------------------------------------
# T2 – single A2A create failure, others persist
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_single_a2a_create_failure_others_persist(
    federation_sync_service: FederationSyncService,
):
    federation_id = PydanticObjectId()
    ok1 = SimpleNamespace(id=None, insert=AsyncMock(side_effect=lambda **kw: setattr(ok1, "id", PydanticObjectId())))
    bad = SimpleNamespace(id=None, insert=AsyncMock(side_effect=Exception("timeout")))
    ok3 = SimpleNamespace(id=None, insert=AsyncMock(side_effect=lambda **kw: setattr(ok3, "id", PydanticObjectId())))

    from registry.services.federation_sync_service import FederationSyncPlan

    sync_plan = FederationSyncPlan(
        summary=FederationApplySummary(createdAgents=3),
        federation_id=federation_id,
        provider_type=FederationProviderType.AWS_AGENTCORE,
        discovered_mcp_count=0,
        discovered_a2a_count=3,
        a2a_creates=[(ok1, "arn:a2a-ok1"), (bad, "arn:a2a-bad"), (ok3, "arn:a2a-ok3")],
    )
    federation_sync_service._get_federation_acl_entries = AsyncMock(return_value=([], True))

    result = await federation_sync_service._apply_sync_plan(sync_plan)

    assert result.summary.mongoApplyFailedAgents == 1
    assert "arn:a2a-ok1" in result.changed_a2a_runtime_arns
    assert "arn:a2a-ok3" in result.changed_a2a_runtime_arns
    assert "arn:a2a-bad" not in result.changed_a2a_runtime_arns


# ---------------------------------------------------------------------------
# T3 – mixed-type failure isolation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_mixed_type_failure_isolation(
    federation_sync_service: FederationSyncService,
):
    federation_id = PydanticObjectId()

    mcp_ok = SimpleNamespace(
        id=None, insert=AsyncMock(side_effect=lambda **kw: setattr(mcp_ok, "id", PydanticObjectId()))
    )
    mcp_bad = SimpleNamespace(id=None, insert=AsyncMock(side_effect=Exception("mcp fail")))
    a2a_ok = SimpleNamespace(
        id=None, insert=AsyncMock(side_effect=lambda **kw: setattr(a2a_ok, "id", PydanticObjectId()))
    )
    a2a_bad = SimpleNamespace(id=None, insert=AsyncMock(side_effect=Exception("a2a fail")))

    from registry.services.federation_sync_service import FederationSyncPlan

    sync_plan = FederationSyncPlan(
        summary=FederationApplySummary(createdMcpServers=2, createdAgents=2),
        federation_id=federation_id,
        provider_type=FederationProviderType.AWS_AGENTCORE,
        discovered_mcp_count=2,
        discovered_a2a_count=2,
        mcp_creates=[(mcp_ok, "arn:mcp-ok"), (mcp_bad, "arn:mcp-bad")],
        a2a_creates=[(a2a_ok, "arn:a2a-ok"), (a2a_bad, "arn:a2a-bad")],
    )
    federation_sync_service._get_federation_acl_entries = AsyncMock(return_value=([], True))

    result = await federation_sync_service._apply_sync_plan(sync_plan)

    assert result.summary.mongoApplyFailedMcpServers == 1
    assert result.summary.mongoApplyFailedAgents == 1
    assert "arn:mcp-ok" in result.changed_mcp_runtime_arns
    assert "arn:a2a-ok" in result.changed_a2a_runtime_arns
    assert len(result.summary.errorMessages) == 2


# ---------------------------------------------------------------------------
# T4 – update failure isolation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_update_failure_isolation(
    federation_sync_service: FederationSyncService,
):
    federation_id = PydanticObjectId()

    existing_ok = SimpleNamespace(
        id=PydanticObjectId(),
        serverName="ok",
        path="/ok",
        tags=[],
        config={},
        numTools=1,
        federationMetadata=None,
        save=AsyncMock(),
    )
    update_ok = SimpleNamespace(
        serverName="ok-v2",
        path="/ok-v2",
        tags=[],
        config={},
        numTools=2,
        federationMetadata=None,
    )
    existing_bad = SimpleNamespace(
        id=PydanticObjectId(),
        serverName="bad",
        path="/bad",
        tags=[],
        config={},
        numTools=1,
        federationMetadata=None,
        save=AsyncMock(side_effect=Exception("save failed")),
    )
    update_bad = SimpleNamespace(
        serverName="bad-v2",
        path="/bad-v2",
        tags=[],
        config={},
        numTools=3,
        federationMetadata=None,
    )

    from registry.services.federation_sync_service import FederationSyncPlan

    sync_plan = FederationSyncPlan(
        summary=FederationApplySummary(updatedMcpServers=2),
        federation_id=federation_id,
        provider_type=FederationProviderType.AWS_AGENTCORE,
        discovered_mcp_count=2,
        discovered_a2a_count=0,
        mcp_updates=[
            (existing_ok, update_ok, "arn:ok"),
            (existing_bad, update_bad, "arn:bad"),
        ],
    )
    federation_sync_service._get_federation_acl_entries = AsyncMock(return_value=([], True))

    result = await federation_sync_service._apply_sync_plan(sync_plan)

    existing_ok.save.assert_awaited_once()
    existing_bad.save.assert_awaited_once()
    assert result.summary.mongoApplyFailedMcpServers == 1
    assert "arn:ok" in result.changed_mcp_runtime_arns
    assert "arn:bad" not in result.changed_mcp_runtime_arns


# ---------------------------------------------------------------------------
# T5 – delete failure isolation (does NOT increment mongoApplyFailed*)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_delete_failure_isolation(
    federation_sync_service: FederationSyncService,
):
    federation_id = PydanticObjectId()
    stale_ok = SimpleNamespace(id=PydanticObjectId(), delete=AsyncMock())
    stale_bad = SimpleNamespace(id=PydanticObjectId(), delete=AsyncMock(side_effect=Exception("delete failed")))

    from registry.services.federation_sync_service import FederationSyncPlan

    sync_plan = FederationSyncPlan(
        summary=FederationApplySummary(deletedMcpServers=2),
        federation_id=federation_id,
        provider_type=FederationProviderType.AWS_AGENTCORE,
        discovered_mcp_count=0,
        discovered_a2a_count=0,
        mcp_deletes=[(stale_ok, "arn:del-ok"), (stale_bad, "arn:del-bad")],
    )
    federation_sync_service._get_federation_acl_entries = AsyncMock(return_value=([], True))

    result = await federation_sync_service._apply_sync_plan(sync_plan)

    stale_ok.delete.assert_awaited_once()
    stale_bad.delete.assert_awaited_once()
    assert result.summary.mongoApplyFailedMcpServers == 0
    assert result.summary.mongoApplyFailedAgents == 0
    assert "arn:del-ok" in result.changed_mcp_runtime_arns
    assert "arn:del-bad" not in result.changed_mcp_runtime_arns
    assert any("arn:del-bad" in msg for msg in result.summary.errorMessages)


# ---------------------------------------------------------------------------
# T6 – failed resource not in changed_runtime_arns
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_failed_resource_not_in_changed_runtime_arns(
    federation_sync_service: FederationSyncService,
):
    federation_id = PydanticObjectId()
    bad_mcp = SimpleNamespace(id=None, insert=AsyncMock(side_effect=Exception("fail")))
    bad_a2a = SimpleNamespace(id=None, insert=AsyncMock(side_effect=Exception("fail")))

    from registry.services.federation_sync_service import FederationSyncPlan

    sync_plan = FederationSyncPlan(
        summary=FederationApplySummary(createdMcpServers=1, createdAgents=1),
        federation_id=federation_id,
        provider_type=FederationProviderType.AWS_AGENTCORE,
        discovered_mcp_count=1,
        discovered_a2a_count=1,
        mcp_creates=[(bad_mcp, "arn:bad-mcp")],
        a2a_creates=[(bad_a2a, "arn:bad-a2a")],
    )
    federation_sync_service._get_federation_acl_entries = AsyncMock(return_value=([], True))

    result = await federation_sync_service._apply_sync_plan(sync_plan)

    assert len(result.changed_mcp_runtime_arns) == 0
    assert len(result.changed_a2a_runtime_arns) == 0
    assert result.summary.mongoApplyFailedMcpServers == 1
    assert result.summary.mongoApplyFailedAgents == 1


# ---------------------------------------------------------------------------
# T13 – bookkeeping transaction failure aborts entire run
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_bookkeeping_failure_aborts_whole_run(
    federation_sync_service: FederationSyncService,
    monkeypatch,
):
    federation = _make_federation(FederationProviderType.AWS_AGENTCORE, {"region": "us-east-1"})
    job = SimpleNamespace(
        id=PydanticObjectId(),
        jobType="full_sync",
        startedAt=datetime.now(UTC),
        discoverySummary=SimpleNamespace(discoveredMcpServers=0, discoveredAgents=0),
    )

    federation_sync_service._discover_entities = AsyncMock(return_value={"mcp_servers": [], "a2a_agents": []})
    federation_sync_service._commit_bookkeeping_transaction = AsyncMock(side_effect=RuntimeError("mark_syncing failed"))
    federation_sync_service._apply_sync_plan = AsyncMock()
    federation_sync_service.federation_job_service.mark_failed = AsyncMock()
    federation_sync_service.federation_crud_service.mark_sync_failed = AsyncMock()
    _patch_mongo_session(monkeypatch)

    with pytest.raises(RuntimeError, match="mark_syncing failed"):
        await federation_sync_service.run_sync(federation=federation, job=job, author_id=_DEFAULT_USER_OBJECT_ID)

    federation_sync_service._apply_sync_plan.assert_not_awaited()
