import json
import logging
import time
from datetime import UTC, datetime
from typing import Any

from beanie import PydanticObjectId

from registry.schemas.server_api_schemas import ServerCreateRequest
from registry.services.access_control_service import acl_service
from registry.services.federation.agentcore_client import AgentCoreFederationClient
from registry.services.server_service import server_service_v1
from registry.services.user_service import user_service
from registry_pkgs.database.decorators import get_current_session, use_transaction
from registry_pkgs.models import A2AAgent, ExtendedMCPServer
from registry_pkgs.models._generated import PrincipalType, ResourceType
from registry_pkgs.models.enums import FederationSource, PermissionBits, RoleBits
from registry_pkgs.vector.repositories.mcp_server_repository import get_mcp_server_repo

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
        federation_client: AgentCoreFederationClient | None = None,
        acl_service_instance=None,
        mcp_server_repo=None,
    ):
        self.federation_client = federation_client or AgentCoreFederationClient()
        self.acl_service = acl_service_instance or acl_service
        self.mcp_server_repo = mcp_server_repo or get_mcp_server_repo()

    async def import_from_runtime(
        self,
        runtime_arns: list[str] | None = None,
        dry_run: bool = False,
        user_id: str | None = None,
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

        discovered = await self.federation_client.discover_runtime_entities(
            runtime_arns=runtime_arns,
            author_id=system_owner_id or viewer_id,
        )
        discovered_mcp = discovered.get("mcp_servers", [])
        discovered_a2a = discovered.get("a2a_agents", [])
        skipped_runtimes = discovered.get("skipped_runtimes", [])

        imported_mcp = 0
        updated_mcp = 0
        skipped_mcp = 0

        imported_a2a = 0
        updated_a2a = 0
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
                if action == "created":
                    imported_mcp += 1
                elif action == "updated":
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
                    imported_a2a += 1
                elif action == "updated":
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

        duration = round(time.time() - start_time, 3)
        return {
            "runtime_filter_count": len(runtime_arns or []),
            "discovered": {
                "mcp_servers": len(discovered_mcp),
                "a2a_agents": len(discovered_a2a),
                "skipped_runtimes": len(skipped_runtimes),
            },
            "imported": {
                "mcp_servers": imported_mcp,
                "a2a_agents": imported_a2a,
            },
            "updated": {
                "mcp_servers": updated_mcp,
                "a2a_agents": updated_a2a,
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
        federation_id = discovered_server.federationId
        if not federation_id:
            raise ValueError("discovered server is missing federationId")

        existing = await ExtendedMCPServer.find_one(
            {
                "federationSource": FederationSource.AGENTCORE,
                "federationId": federation_id,
            }
        )

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
                    "action": "updated",
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
                "action": "created",
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
            num_tools=discovered_server.numTools or 0,
            auth_provider=config.get("authProvider"),
            transport=config.get("type"),
            requires_oauth=bool(config.get("requiresOAuth", False)),
            timeout=config.get("timeout"),
            init_timeout=config.get("initDuration"),
            headers=config.get("headers"),
            oauth=config.get("oauth"),
            enabled=True,
        )
        created_server = await server_service_v1.create_server(
            data=create_request,
            user_id=str(owner_id),
            skip_post_registration_checks=True,
        )

        now = datetime.now(UTC)
        created_server.serverName = discovered_server.serverName
        created_server.path = discovered_server.path or created_server.path
        created_server.tags = list(discovered_server.tags or [])
        created_server.numTools = discovered_server.numTools
        merged_config = dict(created_server.config or {})
        merged_config.update(config)
        created_server.config = merged_config
        created_server.federationSource = FederationSource.AGENTCORE
        created_server.federationId = discovered_server.federationId
        created_server.federationGatewayArn = discovered_server.federationGatewayArn
        created_server.federationSyncedAt = now
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

        await self.mcp_server_repo.sync_server_to_vector_db(created_server, is_delete=False)
        return created_server

    async def _import_single_a2a_agent(
        self,
        discovered_agent: A2AAgent,
        owner_id: PydanticObjectId | None,
        viewer_id: PydanticObjectId | None,
        dry_run: bool,
    ) -> dict[str, Any]:
        federation_id = discovered_agent.federationId
        if not federation_id:
            raise ValueError("discovered A2A agent is missing federationId")

        existing = await A2AAgent.find_one(
            {
                "federationSource": FederationSource.AGENTCORE,
                "federationId": federation_id,
            }
        )

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
                    "action": "updated",
                    "agent_name": agent_name,
                    "agent_id": str(existing.id) if existing.id else None,
                    "changes": changes,
                    "error": None,
                }

            applied = await self._update_a2a_agent(existing=existing, new_data=discovered_agent, changes=changes)
            await self._ensure_acl_permissions(
                resource_type=ResourceType.AGENT,
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
                "action": "created",
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
        discovered_agent.federationSource = FederationSource.AGENTCORE
        discovered_agent.federationSyncedAt = now
        discovered_agent.createdAt = discovered_agent.createdAt or now
        discovered_agent.updatedAt = now
        await discovered_agent.insert(session=self._get_current_session_or_none())

        await self._ensure_acl_permissions(
            resource_type=ResourceType.AGENT,
            resource_id=discovered_agent.id,
            owner_id=owner_id,
            viewer_id=viewer_id,
            dry_run=False,
        )
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
        existing.federationSource = FederationSource.AGENTCORE
        existing.federationId = new_data.federationId
        existing.federationGatewayArn = new_data.federationGatewayArn
        existing.federationSyncedAt = datetime.now(UTC)
        existing.federationMetadata = new_data.federationMetadata
        existing.updatedAt = datetime.now(UTC)
        await existing.save(session=self._get_current_session_or_none())
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
        existing.status = new_data.status or existing.status
        existing.numTools = new_data.numTools
        existing.federationSource = FederationSource.AGENTCORE
        existing.federationId = new_data.federationId
        existing.federationGatewayArn = new_data.federationGatewayArn
        existing.federationSyncedAt = datetime.now(UTC)
        existing.federationMetadata = new_data.federationMetadata
        existing.updatedAt = datetime.now(UTC)

        await existing.save(session=self._get_current_session_or_none())

        if detected_changes:
            await self.mcp_server_repo.sync_server_to_vector_db(existing)

        return detected_changes

    def _detect_changes(
        self,
        existing: ExtendedMCPServer,
        new_data: ExtendedMCPServer,
    ) -> list[str]:
        """
        Detect meaningful changes between existing and newly discovered server.
        """
        changes: list[str] = []
        self._append_change(changes, "serverName", existing.serverName, new_data.serverName)
        self._append_change(changes, "path", existing.path, new_data.path)
        self._append_change(changes, "tags", sorted(existing.tags or []), sorted(new_data.tags or []))
        self._append_change(changes, "config", existing.config or {}, new_data.config or {})
        self._append_change(changes, "status", existing.status, new_data.status)
        self._append_change(
            changes, "federationGatewayArn", existing.federationGatewayArn, new_data.federationGatewayArn
        )
        self._append_change(
            changes, "federationMetadata", existing.federationMetadata or {}, new_data.federationMetadata or {}
        )
        return changes

    def _append_change(self, changes: list[str], field: str, old: Any, new: Any) -> None:
        if old == new:
            return
        changes.append(f"{field}: {self._to_json(old)} -> {self._to_json(new)}")

    def _detect_a2a_changes(self, existing: A2AAgent, new_data: A2AAgent) -> list[str]:
        changes: list[str] = []
        self._append_change(changes, "path", existing.path, new_data.path)
        self._append_change(
            changes, "card", existing.card.model_dump(mode="json"), new_data.card.model_dump(mode="json")
        )
        self._append_change(changes, "tags", sorted(existing.tags or []), sorted(new_data.tags or []))
        self._append_change(changes, "status", existing.status, new_data.status)
        self._append_change(changes, "isEnabled", existing.isEnabled, new_data.isEnabled)
        old_wk = existing.wellKnown.model_dump(mode="json") if existing.wellKnown else None
        new_wk = new_data.wellKnown.model_dump(mode="json") if new_data.wellKnown else None
        self._append_change(changes, "wellKnown", old_wk, new_wk)
        self._append_change(
            changes, "federationGatewayArn", existing.federationGatewayArn, new_data.federationGatewayArn
        )
        self._append_change(
            changes, "federationMetadata", existing.federationMetadata or {}, new_data.federationMetadata or {}
        )
        return changes

    def _to_json(self, value: Any) -> str:
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        except Exception:
            return str(value)

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

        user = await user_service.get_user_by_user_id(user_id)
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


agentcore_import_service = AgentCoreImportService()
