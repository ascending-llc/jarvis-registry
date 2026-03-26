import logging
from datetime import UTC, datetime
from typing import Any

from registry_pkgs.database.decorators import use_transaction
from registry_pkgs.models import A2AAgent, ExtendedMCPServer
from registry_pkgs.models.enums import (
    FederationJobPhase,
    FederationProviderType,
    FederationSyncStatus,
)
from registry_pkgs.models.federation import (
    Federation,
    FederationLastSync,
    FederationLastSyncSummary,
    FederationStats,
)
from registry_pkgs.models.federation_sync_job import FederationApplySummary, FederationSyncJob

from .federation.sync_handlers import (
    AwsAgentCoreSyncHandler,
    AzureAiFoundrySyncHandler,
    BaseFederationSyncHandler,
)
from .federation_crud_service import FederationCrudService
from .federation_job_service import FederationJobService

logger = logging.getLogger(__name__)


def _enum_value(value):
    return value.value if hasattr(value, "value") else value


class FederationSyncService:
    def __init__(
        self,
        federation_crud_service: FederationCrudService,
        federation_job_service: FederationJobService,
        mcp_server_repo,
        a2a_agent_repo,
        acl_service,
        user_service,
    ):
        self.federation_crud_service = federation_crud_service
        self.federation_job_service = federation_job_service
        self.mcp_server_repo = mcp_server_repo
        self.a2a_agent_repo = a2a_agent_repo
        self.acl_service = acl_service
        self.user_service = user_service

        self.sync_handlers: dict[FederationProviderType, BaseFederationSyncHandler] = {
            FederationProviderType.AWS_AGENTCORE: AwsAgentCoreSyncHandler(),
            FederationProviderType.AZURE_AI_FOUNDRY: AzureAiFoundrySyncHandler(),
        }

    def get_sync_handler(self, provider_type: FederationProviderType) -> BaseFederationSyncHandler:
        handler = self.sync_handlers.get(provider_type)
        if handler is None:
            raise ValueError(f"Unsupported federation provider type: {provider_type}")
        return handler

    async def _discover_entities(self, federation: Federation) -> dict[str, list[Any]]:
        # Provider dispatch happens here. The federation already owns the
        # provider type and normalized provider config, so the sync service only
        # needs to select the correct handler and delegate discovery.
        handler = self.get_sync_handler(federation.providerType)
        logger.info("Dispatching federation %s sync to provider handler %s", federation.id, handler.__class__.__name__)
        return await handler.discover_entities(federation)

    async def run_sync(
        self,
        federation: Federation,
        job: FederationSyncJob,
        user_id: str | None,
    ) -> FederationSyncJob:
        """
        Sync execution follows a fixed flow:
            1. mark job/federation as running
            2. discover remote resources
            3. apply the diff into local MongoDB documents
            4. persist stats and lastSync
            Any exception moves both the federation and the job into failed state.
        """
        await self.federation_job_service.mark_syncing(job, FederationJobPhase.DISCOVERING)
        await self.federation_crud_service.mark_syncing(federation)

        try:
            # 1) discovery（事务外）
            discovered = await self._discover_entities(federation)
            discovered_mcp = discovered.get("mcp_servers", [])
            discovered_a2a = discovered.get("a2a_agents", [])

            await self.federation_job_service.update_discovery_summary(
                job,
                discovered_mcp_servers=len(discovered_mcp),
                discovered_agents=len(discovered_a2a),
            )

            # 2) apply（事务内）
            await self.federation_job_service.mark_syncing(job, FederationJobPhase.APPLYING)
            apply_summary = await self._apply_sync_transaction(
                federation=federation,
                job=job,
                discovered_mcp=discovered_mcp,
                discovered_a2a=discovered_a2a,
                user_id=user_id,
            )

            await self.federation_job_service.update_apply_summary(job, apply_summary)

            # 3) 统计与 lastSync（事务后保存）
            mcp_count = await ExtendedMCPServer.find(
                {"federationRefId": federation.id, "status": {"$ne": "deleted"}}
            ).count()
            agent_count = await A2AAgent.find({"federationRefId": federation.id, "status": {"$ne": "deleted"}}).count()

            mcp_servers = await ExtendedMCPServer.find(
                {"federationRefId": federation.id, "status": {"$ne": "deleted"}}
            ).to_list()
            tool_count = sum(int(server.numTools or 0) for server in mcp_servers)

            stats = FederationStats(
                mcpServerCount=mcp_count,
                agentCount=agent_count,
                toolCount=tool_count,
                importedTotal=mcp_count + agent_count,
            )

            last_sync = FederationLastSync(
                jobId=job.id,
                jobType=job.jobType,
                status=FederationSyncStatus.SUCCESS,
                startedAt=job.startedAt,
                finishedAt=datetime.now(UTC),
                summary=FederationLastSyncSummary(
                    discoveredMcpServers=job.discoverySummary.discoveredMcpServers,
                    discoveredAgents=job.discoverySummary.discoveredAgents,
                    createdMcpServers=apply_summary.createdMcpServers,
                    updatedMcpServers=apply_summary.updatedMcpServers,
                    deletedMcpServers=apply_summary.deletedMcpServers,
                    unchangedMcpServers=apply_summary.unchangedMcpServers,
                    createdAgents=apply_summary.createdAgents,
                    updatedAgents=apply_summary.updatedAgents,
                    deletedAgents=apply_summary.deletedAgents,
                    unchangedAgents=apply_summary.unchangedAgents,
                    errors=0,
                ),
            )

            await self.federation_crud_service.mark_sync_success(federation, last_sync, stats)
            await self.federation_job_service.mark_success(job)
            return job

        except Exception as exc:
            await self.federation_crud_service.mark_sync_failed(federation, str(exc))
            await self.federation_job_service.mark_failed(job, FederationJobPhase.FAILED, str(exc))
            raise

    @use_transaction
    async def _apply_sync_transaction(
        self,
        federation: Federation,
        job: FederationSyncJob,
        discovered_mcp: list[Any],
        discovered_a2a: list[Any],
        user_id: str | None,
    ) -> FederationApplySummary:
        """
        Inside the transaction, only mutate MongoDB source-of-truth documents.
        """
        apply_summary = FederationApplySummary()

        # -------- MCP --------
        existing_mcp = await ExtendedMCPServer.find({"federationRefId": federation.id}).to_list()
        existing_mcp_by_remote = {
            self._extract_runtime_arn(item.federationMetadata): item
            for item in existing_mcp
            if self._extract_runtime_arn(item.federationMetadata)
        }

        discovered_mcp_ids: set[str] = set()

        for item in discovered_mcp:
            remote_id = self._extract_runtime_arn(item.federationMetadata)
            if not remote_id:
                continue

            discovered_mcp_ids.add(remote_id)
            existing = existing_mcp_by_remote.get(remote_id)

            if existing is None:
                server = item
                server.federationRefId = federation.id
                server.federationMetadata = server.federationMetadata or {}
                server.federationMetadata["providerType"] = _enum_value(federation.providerType)
                await server.insert()
                apply_summary.createdMcpServers += 1
            else:
                old_version = (existing.federationMetadata or {}).get("runtimeVersion")
                new_version = (item.federationMetadata or {}).get("runtimeVersion")

                if str(old_version) == str(new_version):
                    apply_summary.unchangedMcpServers += 1
                else:
                    existing.serverName = item.serverName
                    existing.path = item.path
                    existing.tags = list(item.tags or [])
                    existing.config = dict(item.config or {})
                    existing.status = item.status or existing.status
                    existing.numTools = item.numTools
                    existing.federationMetadata = item.federationMetadata
                    await existing.save()
                    apply_summary.updatedMcpServers += 1

        stale_mcp = [
            item
            for item in existing_mcp
            if self._extract_runtime_arn(item.federationMetadata)
            and self._extract_runtime_arn(item.federationMetadata) not in discovered_mcp_ids
        ]
        for stale in stale_mcp:
            await stale.delete()
            apply_summary.deletedMcpServers += 1

        # -------- A2A --------
        existing_a2a = await A2AAgent.find({"federationRefId": federation.id}).to_list()
        existing_a2a_by_remote = {
            self._extract_runtime_arn(item.federationMetadata): item
            for item in existing_a2a
            if self._extract_runtime_arn(item.federationMetadata)
        }

        discovered_a2a_ids: set[str] = set()

        for item in discovered_a2a:
            remote_id = self._extract_runtime_arn(item.federationMetadata)
            if not remote_id:
                continue

            discovered_a2a_ids.add(remote_id)
            existing = existing_a2a_by_remote.get(remote_id)

            if existing is None:
                agent = item
                agent.federationRefId = federation.id
                agent.federationMetadata = agent.federationMetadata or {}
                agent.federationMetadata["providerType"] = _enum_value(federation.providerType)
                await agent.insert()
                apply_summary.createdAgents += 1
            else:
                old_version = (existing.federationMetadata or {}).get("runtimeVersion")
                new_version = (item.federationMetadata or {}).get("runtimeVersion")

                if str(old_version) == str(new_version):
                    apply_summary.unchangedAgents += 1
                else:
                    existing.path = item.path
                    existing.card = item.card
                    existing.tags = list(item.tags or [])
                    existing.status = item.status
                    existing.isEnabled = item.isEnabled
                    existing.wellKnown = item.wellKnown
                    existing.federationMetadata = item.federationMetadata
                    await existing.save()
                    apply_summary.updatedAgents += 1

        stale_a2a = [
            item
            for item in existing_a2a
            if self._extract_runtime_arn(item.federationMetadata)
            and self._extract_runtime_arn(item.federationMetadata) not in discovered_a2a_ids
        ]
        for stale in stale_a2a:
            await stale.delete()
            apply_summary.deletedAgents += 1

        return apply_summary

    async def run_delete(
        self,
        federation: Federation,
        job: FederationSyncJob,
    ) -> FederationSyncJob:
        await self.federation_job_service.mark_syncing(job, FederationJobPhase.APPLYING)

        try:
            await self._delete_transaction(federation)

            # 如果你们当前仍旧直接删向量库，可在事务外处理
            stats = FederationStats(mcpServerCount=0, agentCount=0, toolCount=0, importedTotal=0)
            last_sync = FederationLastSync(
                jobId=job.id,
                jobType=job.jobType,
                status=FederationSyncStatus.SUCCESS,
                startedAt=job.startedAt,
                finishedAt=datetime.now(UTC),
            )

            await self.federation_crud_service.mark_sync_success(federation, last_sync, stats)
            await self.federation_crud_service.mark_deleted(federation)
            await self.federation_job_service.mark_success(job)
            return job
        except Exception as exc:
            await self.federation_crud_service.mark_delete_failed(federation, str(exc))
            await self.federation_job_service.mark_failed(job, FederationJobPhase.FAILED, str(exc))
            raise

    @use_transaction
    async def _delete_transaction(self, federation: Federation) -> None:
        mcp_list = await ExtendedMCPServer.find({"federationRefId": federation.id}).to_list()
        for item in mcp_list:
            await item.delete()

        a2a_list = await A2AAgent.find({"federationRefId": federation.id}).to_list()
        for item in a2a_list:
            await item.delete()

    @staticmethod
    def _extract_runtime_arn(metadata: dict[str, Any] | None) -> str | None:
        if not metadata:
            return None
        runtime_arn = metadata.get("runtimeArn")
        return str(runtime_arn) if runtime_arn else None
