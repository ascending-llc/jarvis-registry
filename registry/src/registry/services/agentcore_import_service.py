import logging
import time
from datetime import UTC, datetime
from typing import Any

from beanie import PydanticObjectId

from registry.services.federation.agentcore_clients import AgentCoreClientProvider
from registry.services.federation.agentcore_discovery import AgentCoreFederationClient
from registry.services.federation.agentcore_runtime import AgentCoreRuntimeInvoker
from registry_pkgs.database.decorators import get_current_session, use_transaction
from registry_pkgs.models import A2AAgent, ExtendedMCPServer, PrincipalType, ResourceType
from registry_pkgs.models.enums import PermissionBits, RoleBits
from registry_pkgs.vector.repositories.a2a_agent_repository import A2AAgentRepository
from registry_pkgs.vector.repositories.mcp_server_repository import MCPServerRepository

from ..core.config import settings
from ..schemas.server_api_schemas import ServerCreateRequest
from .access_control_service import ACLService
from .server_service import ServerServiceV1
from .user_service import UserService

logger = logging.getLogger(__name__)


class AgentCoreImportService:
    """
    Batch import service for AgentCore-federated MCP servers.

    Current scope:
    - Imports MCP servers and A2A agents discovered from AgentCore runtimes
    - Supports dry-run preview mode
    - AgentCore is source of truth for updates
    """

    def __init__(
        self,
        acl_service_instance: ACLService,
        server_service: ServerServiceV1,
        user_service_instance: UserService,
        mcp_server_repo: MCPServerRepository,
        a2a_agent_repo: A2AAgentRepository,
        federation_client: AgentCoreFederationClient | None = None,
        agentcore_client_provider: AgentCoreClientProvider | None = None,
        runtime_invoker: AgentCoreRuntimeInvoker | None = None,
    ):
        self.acl_service = acl_service_instance
        self.server_service = server_service
        self.user_service = user_service_instance
        self.mcp_server_repo = mcp_server_repo
        self.a2a_agent_repo = a2a_agent_repo

        client_provider = agentcore_client_provider or AgentCoreClientProvider()
        invoker = runtime_invoker or AgentCoreRuntimeInvoker(
            client_provider=client_provider,
            extract_region_from_arn=AgentCoreFederationClient.extract_region_from_arn,
        )
        self.federation_client = federation_client or AgentCoreFederationClient(
            client_provider=client_provider,
            runtime_invoker=invoker,
        )

    async def import_from_runtime(
        self,
        dry_run: bool = False,
        user_id: str | None = None,
        aws_region: str | None = None,
        runtime_arn: str | None = None,
    ) -> dict[str, Any]:
        """
        Import MCP + A2A entities from AgentCore runtimes.

        HTTP/UNKNOWN runtimes are skipped and returned in skipped_runtimes.
        """
        start_time = time.time()
        errors: list[str] = []
        mcp_results: list[dict[str, Any]] = []
        a2a_results: list[dict[str, Any]] = []

        system_owner_id, viewer_id = await self._resolve_identities(user_id=user_id, dry_run=dry_run)

        runtime_arns = [runtime_arn] if runtime_arn else None
        resolved_region = aws_region or settings.aws_region or "us-east-1"
        discovered = await self.federation_client.discover_runtime_entities(
            runtime_arns=runtime_arns,
            author_id=system_owner_id or viewer_id,
            region=resolved_region,
        )
        discovered_mcp = discovered.get("mcp_servers", [])
        discovered_a2a = discovered.get("a2a_agents", [])
        skipped_runtimes = discovered.get("skipped_runtimes", [])

        created_mcp = 0
        updated_mcp = 0
        deleted_mcp = 0
        skipped_mcp = 0

        created_a2a = 0
        updated_a2a = 0
        deleted_a2a = 0
        skipped_a2a = 0

        for discovered_server in discovered_mcp:
            try:
                if dry_run:
                    result = await self._import_single_server(
                        discovered_server=discovered_server,
                        owner_id=system_owner_id,
                        viewer_id=viewer_id,
                        dry_run=True,
                    )
                else:
                    result = await self._import_single_server_in_transaction(
                        discovered_server=discovered_server,
                        owner_id=system_owner_id,
                        viewer_id=viewer_id,
                    )
                mcp_results.append(result)
                action = result.get("action")
                if action in {"created", "would_create"}:
                    created_mcp += 1
                elif action in {"updated", "would_update"}:
                    updated_mcp += 1
                elif action == "skipped":
                    skipped_mcp += 1
                elif action == "error":
                    skipped_mcp += 1
                    if result.get("error"):
                        errors.append(str(result["error"]))
            except Exception as exc:
                server_name = getattr(discovered_server, "serverName", "unknown")
                message = f"Failed to import server '{server_name}': {exc}"
                logger.error(message, exc_info=True)
                errors.append(message)
                skipped_mcp += 1
                mcp_results.append(
                    {
                        "action": "error",
                        "server_name": server_name,
                        "server_id": None,
                        "changes": [],
                        "error": str(exc),
                    }
                )

        for discovered_agent in discovered_a2a:
            try:
                if dry_run:
                    result = await self._import_single_a2a_agent(
                        discovered_agent=discovered_agent,
                        owner_id=system_owner_id,
                        viewer_id=viewer_id,
                        dry_run=True,
                    )
                else:
                    result = await self._import_single_a2a_agent_in_transaction(
                        discovered_agent=discovered_agent,
                        owner_id=system_owner_id,
                        viewer_id=viewer_id,
                    )
                a2a_results.append(result)
                action = result.get("action")
                if action == "created":
                    created_a2a += 1
                elif action == "updated":
                    updated_a2a += 1
                elif action == "would_create":
                    created_a2a += 1
                elif action == "would_update":
                    updated_a2a += 1
                elif action == "skipped":
                    skipped_a2a += 1
                elif action == "error":
                    skipped_a2a += 1
                    if result.get("error"):
                        errors.append(str(result["error"]))
            except Exception as exc:
                agent_name = getattr(getattr(discovered_agent, "card", None), "name", "unknown")
                message = f"Failed to import A2A agent '{agent_name}': {exc}"
                logger.error(message, exc_info=True)
                errors.append(message)
                skipped_a2a += 1
                a2a_results.append(
                    {
                        "action": "error",
                        "agent_name": agent_name,
                        "agent_id": None,
                        "changes": [],
                        "error": str(exc),
                    }
                )

        discovered_mcp_ids = {
            self.extract_runtime_arn(item.federationMetadata)
            for item in discovered_mcp
            if self.extract_runtime_arn(item.federationMetadata)
        }
        discovered_a2a_ids = {
            self.extract_runtime_arn(item.federationMetadata)
            for item in discovered_a2a
            if self.extract_runtime_arn(item.federationMetadata)
        }
        all_discovered_runtime_arns = (
            discovered_mcp_ids
            | discovered_a2a_ids
            | {item.get("runtimeArn") for item in skipped_runtimes if item.get("runtimeArn")}
        )

        stale_mcp, stale_a2a = await self._collect_stale_entities(
            all_discovered_runtime_arns=all_discovered_runtime_arns,
            discovered_mcp_ids=discovered_mcp_ids,
            discovered_a2a_ids=discovered_a2a_ids,
        )

        for stale_server in stale_mcp:
            try:
                result = await self._delete_stale_server(stale_server=stale_server, dry_run=dry_run)
                mcp_results.append(result)
                if result["action"] in {"deleted", "would_delete"}:
                    deleted_mcp += 1
                elif result["action"] == "error":
                    skipped_mcp += 1
                    if result.get("error"):
                        errors.append(str(result["error"]))
            except Exception as exc:
                message = f"Failed to delete stale MCP server '{stale_server.serverName}': {exc}"
                logger.error(message, exc_info=True)
                errors.append(message)
                skipped_mcp += 1
                mcp_results.append(
                    {
                        "action": "error",
                        "server_name": stale_server.serverName,
                        "server_id": str(stale_server.id) if stale_server.id else None,
                        "changes": [],
                        "error": str(exc),
                    }
                )

        for stale_agent in stale_a2a:
            try:
                result = await self._delete_stale_a2a_agent(stale_agent=stale_agent, dry_run=dry_run)
                a2a_results.append(result)
                if result["action"] in {"deleted", "would_delete"}:
                    deleted_a2a += 1
                elif result["action"] == "error":
                    skipped_a2a += 1
                    if result.get("error"):
                        errors.append(str(result["error"]))
            except Exception as exc:
                agent_name = stale_agent.card.name if stale_agent.card else "unknown"
                message = f"Failed to delete stale A2A agent '{agent_name}': {exc}"
                logger.error(message, exc_info=True)
                errors.append(message)
                skipped_a2a += 1
                a2a_results.append(
                    {
                        "action": "error",
                        "agent_name": agent_name,
                        "agent_id": str(stale_agent.id) if stale_agent.id else None,
                        "changes": [],
                        "error": str(exc),
                    }
                )

        duration = round(time.time() - start_time, 3)
        return {
            "runtime_filter_count": len(runtime_arns or []),
            "discovered": {
                "mcp_servers": len(discovered_mcp),
                "a2a_agents": len(discovered_a2a),
                "skipped_runtimes": len(skipped_runtimes),
            },
            "created": {
                "mcp_servers": created_mcp,
                "a2a_agents": created_a2a,
            },
            "updated": {
                "mcp_servers": updated_mcp,
                "a2a_agents": updated_a2a,
            },
            "deleted": {
                "mcp_servers": deleted_mcp,
                "a2a_agents": deleted_a2a,
            },
            "skipped": {
                "mcp_servers": skipped_mcp,
                "a2a_agents": skipped_a2a,
            },
            "errors": errors,
            "mcp_servers": mcp_results,
            "a2a_agents": a2a_results,
            "skipped_runtimes": skipped_runtimes,
            "duration_seconds": duration,
        }

    async def _import_single_server(
        self,
        discovered_server: ExtendedMCPServer,
        owner_id: PydanticObjectId | None,
        viewer_id: PydanticObjectId | None,
        dry_run: bool,
    ) -> dict[str, Any]:
        """
        Import single server with conflict handling.
        """
        runtime_arn = self.extract_runtime_arn(discovered_server.federationMetadata)
        if not runtime_arn:
            raise ValueError("discovered server is missing runtimeArn in federationMetadata")

        existing = await ExtendedMCPServer.find_one({"federationMetadata.runtimeArn": runtime_arn})

        if existing:
            changes = self._detect_changes(existing, discovered_server)
            if not changes:
                return {
                    "action": "skipped",
                    "server_name": existing.serverName,
                    "server_id": str(existing.id) if existing.id else None,
                    "changes": [],
                    "error": None,
                }

            if dry_run:
                return {
                    "action": "would_update",
                    "server_name": existing.serverName,
                    "server_id": str(existing.id) if existing.id else None,
                    "changes": changes,
                    "error": None,
                }

            applied = await self._update_server(existing=existing, new_data=discovered_server, changes=changes)
            await self._ensure_acl_permissions(
                resource_type=ResourceType.MCPSERVER,
                resource_id=existing.id,
                owner_id=owner_id,
                viewer_id=viewer_id,
                dry_run=dry_run,
            )
            return {
                "action": "updated",
                "server_name": existing.serverName,
                "server_id": str(existing.id) if existing.id else None,
                "changes": applied,
                "error": None,
            }

        # Create new server
        if dry_run:
            return {
                "action": "would_create",
                "server_name": discovered_server.serverName,
                "server_id": None,
                "changes": ["new server"],
                "error": None,
            }

        created = await self._create_server(
            discovered_server=discovered_server,
            owner_id=owner_id,
            viewer_id=viewer_id,
        )
        return {
            "action": "created",
            "server_name": created.serverName,
            "server_id": str(created.id) if created.id else None,
            "changes": ["new server"],
            "error": None,
        }

    async def _create_server(
        self,
        discovered_server: ExtendedMCPServer,
        owner_id: PydanticObjectId | None,
        viewer_id: PydanticObjectId | None,
    ) -> ExtendedMCPServer:
        """
        Create a new federated server and wire ACL/vector sync.
        """
        if owner_id is None:
            raise ValueError("owner_id is required for non-dry-run imports")

        config = discovered_server.config or {}
        create_request = ServerCreateRequest(
            title=config.get("title") or discovered_server.serverName,
            path=discovered_server.path or f"/agentcore/{discovered_server.serverName}",
            description=config.get("description", ""),
            url=config.get("url"),
            tags=list(discovered_server.tags or []),
            numTools=discovered_server.numTools or 0,
            authProvider=config.get("authProvider"),
            type=config.get("type"),
            requiresOauth=bool(config.get("requiresOAuth", False)),
            timeout=config.get("timeout"),
            initTimeout=config.get("initDuration"),
            headers=config.get("headers"),
            oauth=config.get("oauth"),
        )
        created_server = await self.server_service.create_server(
            data=create_request,
            user_id=str(owner_id),
            skip_post_registration_checks=True,
        )

        now = datetime.now(UTC)
        created_server.serverName = discovered_server.serverName
        # create_server() recomputes numTools from toolFunctions. For AgentCore imports
        # we skip post-registration discovery and persist the discovered runtime count.
        created_server.numTools = discovered_server.numTools
        merged_config = dict(created_server.config or {})
        merged_config.update(config)
        if "requiresOAuth" not in merged_config and "requiresOauth" in merged_config:
            merged_config["requiresOAuth"] = merged_config.get("requiresOauth")
        merged_config.pop("requiresOauth", None)
        merged_config["enabled"] = discovered_server.status == "active"
        created_server.config = merged_config
        created_server.status = discovered_server.status or created_server.status
        created_server.federationMetadata = discovered_server.federationMetadata
        created_server.updatedAt = now
        await created_server.save(session=self._get_current_session_or_none())

        await self._ensure_acl_permissions(
            resource_type=ResourceType.MCPSERVER,
            resource_id=created_server.id,
            owner_id=owner_id,
            viewer_id=viewer_id,
            dry_run=False,
        )

        await self.mcp_server_repo.sync_server_to_vector_db(created_server)
        return created_server

    async def _import_single_a2a_agent(
        self,
        discovered_agent: A2AAgent,
        owner_id: PydanticObjectId | None,
        viewer_id: PydanticObjectId | None,
        dry_run: bool,
    ) -> dict[str, Any]:
        runtime_arn = self.extract_runtime_arn(discovered_agent.federationMetadata)
        if not runtime_arn:
            raise ValueError("discovered A2A agent is missing runtimeArn in federationMetadata")

        existing = await A2AAgent.find_one({"federationMetadata.runtimeArn": runtime_arn})

        agent_name = discovered_agent.card.name
        if existing:
            changes = self._detect_a2a_changes(existing, discovered_agent)
            if not changes:
                return {
                    "action": "skipped",
                    "agent_name": agent_name,
                    "agent_id": str(existing.id) if existing.id else None,
                    "changes": [],
                    "error": None,
                }

            if dry_run:
                return {
                    "action": "would_update",
                    "agent_name": agent_name,
                    "agent_id": str(existing.id) if existing.id else None,
                    "changes": changes,
                    "error": None,
                }

            applied = await self._update_a2a_agent(existing=existing, new_data=discovered_agent, changes=changes)
            await self._ensure_acl_permissions(
                resource_type=ResourceType.REMOTE_AGENT,
                resource_id=existing.id,
                owner_id=owner_id,
                viewer_id=viewer_id,
                dry_run=dry_run,
            )
            return {
                "action": "updated",
                "agent_name": agent_name,
                "agent_id": str(existing.id) if existing.id else None,
                "changes": applied,
                "error": None,
            }

        if dry_run:
            return {
                "action": "would_create",
                "agent_name": agent_name,
                "agent_id": None,
                "changes": ["new agent"],
                "error": None,
            }

        created = await self._create_a2a_agent(
            discovered_agent=discovered_agent,
            owner_id=owner_id,
            viewer_id=viewer_id,
        )
        return {
            "action": "created",
            "agent_name": agent_name,
            "agent_id": str(created.id) if created.id else None,
            "changes": ["new agent"],
            "error": None,
        }

    async def _create_a2a_agent(
        self,
        discovered_agent: A2AAgent,
        owner_id: PydanticObjectId | None,
        viewer_id: PydanticObjectId | None,
    ) -> A2AAgent:
        if owner_id is None:
            raise ValueError("owner_id is required for non-dry-run imports")

        now = datetime.now(UTC)
        discovered_agent.author = owner_id
        discovered_agent.createdAt = discovered_agent.createdAt or now
        discovered_agent.updatedAt = now
        await discovered_agent.insert(session=self._get_current_session_or_none())

        await self._ensure_acl_permissions(
            resource_type=ResourceType.REMOTE_AGENT,
            resource_id=discovered_agent.id,
            owner_id=owner_id,
            viewer_id=viewer_id,
            dry_run=False,
        )
        await self.a2a_agent_repo.sync_agent_to_vector_db(discovered_agent, is_delete=False)
        return discovered_agent

    async def _update_a2a_agent(
        self,
        existing: A2AAgent,
        new_data: A2AAgent,
        changes: list[str] | None = None,
    ) -> list[str]:
        detected_changes = changes or self._detect_a2a_changes(existing, new_data)
        for change in detected_changes:
            logger.info("AgentCore overwrite: %s (a2a_agent=%s)", change, existing.card.name)

        existing.path = new_data.path
        existing.card = new_data.card
        existing.tags = list(new_data.tags or [])
        existing.status = new_data.status
        existing.isEnabled = new_data.isEnabled
        existing.wellKnown = new_data.wellKnown
        existing.federationMetadata = new_data.federationMetadata
        existing.updatedAt = datetime.now(UTC)
        await existing.save(session=self._get_current_session_or_none())
        if detected_changes:
            await self.a2a_agent_repo.sync_agent_to_vector_db(existing, is_delete=True)
        return detected_changes

    async def _update_server(
        self,
        existing: ExtendedMCPServer,
        new_data: ExtendedMCPServer,
        changes: list[str] | None = None,
    ) -> list[str]:
        """
        Overwrite existing server from AgentCore source-of-truth.
        """
        detected_changes = changes or self._detect_changes(existing, new_data)

        for change in detected_changes:
            logger.info("AgentCore overwrite: %s (server=%s)", change, existing.serverName)

        existing.serverName = new_data.serverName
        existing.path = new_data.path
        existing.tags = list(new_data.tags or [])
        existing.config = dict(new_data.config or {})
        if "requiresOAuth" not in existing.config and "requiresOauth" in existing.config:
            existing.config["requiresOAuth"] = existing.config.get("requiresOauth")
        existing.config.pop("requiresOauth", None)
        existing.status = new_data.status or existing.status
        existing.config["enabled"] = existing.status == "active"
        existing.numTools = new_data.numTools
        existing.federationMetadata = new_data.federationMetadata
        existing.updatedAt = datetime.now(UTC)

        await existing.save(session=self._get_current_session_or_none())

        if detected_changes:
            await self.mcp_server_repo.sync_server_to_vector_db(existing, is_delete=True)

        return detected_changes

    def _detect_changes(
        self,
        existing: ExtendedMCPServer,
        new_data: ExtendedMCPServer,
    ) -> list[str]:
        return self.detect_runtime_version_change(existing.federationMetadata, new_data.federationMetadata)

    def _detect_a2a_changes(self, existing: A2AAgent, new_data: A2AAgent) -> list[str]:
        return self.detect_runtime_version_change(existing.federationMetadata, new_data.federationMetadata)

    @staticmethod
    def extract_runtime_version(metadata: dict[str, Any] | None) -> str | None:
        if not metadata:
            return None
        version = metadata.get("runtimeVersion")
        if version is None:
            return None
        return str(version)

    @classmethod
    def detect_runtime_version_change(
        cls,
        existing_metadata: dict[str, Any] | None,
        new_metadata: dict[str, Any] | None,
    ) -> list[str]:
        old_version = cls.extract_runtime_version(existing_metadata)
        new_version = cls.extract_runtime_version(new_metadata)
        if old_version == new_version:
            return []
        return [f"runtimeVersion: {old_version} -> {new_version}"]

    async def _collect_stale_entities(
        self,
        *,
        all_discovered_runtime_arns: set[str],
        discovered_mcp_ids: set[str],
        discovered_a2a_ids: set[str],
    ) -> tuple[list[ExtendedMCPServer], list[A2AAgent]]:
        stale_mcp: list[ExtendedMCPServer] = []
        stale_a2a: list[A2AAgent] = []

        existing_mcp = await ExtendedMCPServer.find(
            {"federationMetadata.runtimeArn": {"$exists": True, "$ne": None}}
        ).to_list()
        existing_a2a = await A2AAgent.find({"federationMetadata.runtimeArn": {"$exists": True, "$ne": None}}).to_list()

        for server in existing_mcp:
            runtime_arn = self.extract_runtime_arn(server.federationMetadata)
            if not runtime_arn:
                continue
            if (server.federationMetadata or {}).get("sourceType") != "runtime":
                continue
            if runtime_arn not in all_discovered_runtime_arns:
                stale_mcp.append(server)
                continue
            if runtime_arn not in discovered_mcp_ids:
                stale_mcp.append(server)

        for agent in existing_a2a:
            runtime_arn = self.extract_runtime_arn(agent.federationMetadata)
            if not runtime_arn:
                continue
            if (agent.federationMetadata or {}).get("sourceType") != "runtime":
                continue
            if runtime_arn not in all_discovered_runtime_arns:
                stale_a2a.append(agent)
                continue
            if runtime_arn not in discovered_a2a_ids:
                stale_a2a.append(agent)

        return stale_mcp, stale_a2a

    async def _delete_stale_server(
        self,
        *,
        stale_server: ExtendedMCPServer,
        dry_run: bool,
    ) -> dict[str, Any]:
        server_name = stale_server.serverName
        server_id = str(stale_server.id) if stale_server.id else None
        runtime_arn = self.extract_runtime_arn(stale_server.federationMetadata)

        if dry_run:
            return {
                "action": "would_delete",
                "server_name": server_name,
                "server_id": server_id,
                "changes": [f"delete stale federated MCP server ({runtime_arn})"],
                "error": None,
            }

        if server_id:
            await self.mcp_server_repo.delete_by_server_id(server_id, server_name)
        await stale_server.delete(session=self._get_current_session_or_none())
        return {
            "action": "deleted",
            "server_name": server_name,
            "server_id": server_id,
            "changes": [f"deleted stale federated MCP server ({runtime_arn})"],
            "error": None,
        }

    async def _delete_stale_a2a_agent(
        self,
        *,
        stale_agent: A2AAgent,
        dry_run: bool,
    ) -> dict[str, Any]:
        agent_name = stale_agent.card.name
        agent_id = str(stale_agent.id) if stale_agent.id else None
        runtime_arn = self.extract_runtime_arn(stale_agent.federationMetadata)

        if dry_run:
            return {
                "action": "would_delete",
                "agent_name": agent_name,
                "agent_id": agent_id,
                "changes": [f"delete stale federated A2A agent ({runtime_arn})"],
                "error": None,
            }

        if agent_id:
            await self.a2a_agent_repo.delete_by_agent_id(agent_id, agent_name)
        await stale_agent.delete(session=self._get_current_session_or_none())
        return {
            "action": "deleted",
            "agent_name": agent_name,
            "agent_id": agent_id,
            "changes": [f"deleted stale federated A2A agent ({runtime_arn})"],
            "error": None,
        }

    @staticmethod
    def extract_runtime_arn(metadata: dict[str, Any] | None) -> str | None:
        if not metadata:
            return None
        runtime_arn = metadata.get("runtimeArn")
        return str(runtime_arn) if runtime_arn else None

    async def _resolve_identities(
        self, user_id: str | None, dry_run: bool
    ) -> tuple[PydanticObjectId | None, PydanticObjectId | None]:
        """
        Resolve import owner/viewer identities from current user.
        """
        if not user_id:
            if dry_run:
                return None, None
            raise ValueError("user_id is required for import")

        user = await self.user_service.get_user_by_user_id(user_id)
        if not user:
            if dry_run:
                return None, None
            raise ValueError(f"user not found for import owner: {user_id}")

        owner_id = user.id
        viewer_id = user.id
        return owner_id, viewer_id

    async def _ensure_acl_permissions(
        self,
        resource_type: ResourceType,
        resource_id: PydanticObjectId,
        owner_id: PydanticObjectId | None,
        viewer_id: PydanticObjectId | None,
        dry_run: bool,
    ) -> None:
        if dry_run:
            return
        if owner_id:
            await self.acl_service.grant_permission(
                principal_type=PrincipalType.USER,
                principal_id=owner_id,
                resource_type=resource_type,
                resource_id=resource_id,
                perm_bits=RoleBits.OWNER,
            )
        if viewer_id and viewer_id != owner_id:
            await self.acl_service.grant_permission(
                principal_type=PrincipalType.USER,
                principal_id=viewer_id,
                resource_type=resource_type,
                resource_id=resource_id,
                perm_bits=PermissionBits.VIEW,
            )

    def _get_current_session_or_none(self):
        try:
            return get_current_session()
        except RuntimeError:
            return None

    @use_transaction
    async def _import_single_server_in_transaction(
        self,
        discovered_server: ExtendedMCPServer,
        owner_id: PydanticObjectId | None,
        viewer_id: PydanticObjectId | None,
    ) -> dict[str, Any]:
        return await self._import_single_server(
            discovered_server=discovered_server,
            owner_id=owner_id,
            viewer_id=viewer_id,
            dry_run=False,
        )

    @use_transaction
    async def _import_single_a2a_agent_in_transaction(
        self,
        discovered_agent: A2AAgent,
        owner_id: PydanticObjectId | None,
        viewer_id: PydanticObjectId | None,
    ) -> dict[str, Any]:
        return await self._import_single_a2a_agent(
            discovered_agent=discovered_agent,
            owner_id=owner_id,
            viewer_id=viewer_id,
            dry_run=False,
        )
