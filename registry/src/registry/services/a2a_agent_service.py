"""
A2A Agent Service - Business logic for A2A Agent Management API

This service handles all A2A agent-related operations using MongoDB, Beanie ODM,
and the official a2a-sdk for protocol compliance.
"""

import logging
import re
from datetime import UTC, datetime
from typing import Any

import httpx
from a2a.client import A2ACardResolver, A2AClientHTTPError
from a2a.types import AgentCard
from beanie import PydanticObjectId

from registry.core.exceptions import (
    A2AAgentCardNotFoundException,
    A2AAgentCardParseException,
    A2AAgentCardTransportException,
    A2AAgentCardUpstreamException,
)
from registry_pkgs.models.a2a_agent import STATUS_ACTIVE, A2AAgent

from ..schemas.a2a_agent_api_schemas import AgentCreateRequest, AgentUpdateRequest

logger = logging.getLogger(__name__)


class A2AAgentService:
    """Service for A2A Agent operations"""

    async def _resolve_agent_card_with_fallback(
        self,
        base_url: str,
        timeout_seconds: float,
    ) -> AgentCard:
        """Fetch agent card from known well-known paths with deterministic error semantics."""
        timeout = httpx.Timeout(timeout_seconds)
        last_404_error: Exception | None = None
        first_non_404_http_error: Exception | None = None
        first_transport_error: Exception | None = None
        first_parse_error: Exception | None = None

        well_known_paths = [".well-known/agent-card.json", ".well-known/agent.json"]

        async with httpx.AsyncClient(timeout=timeout) as client:
            for path in well_known_paths:
                try:
                    resolver = A2ACardResolver(
                        base_url=base_url,
                        httpx_client=client,
                        agent_card_path=path,
                    )
                    agent_card = await resolver.get_agent_card()
                    logger.info(f"Successfully fetched agent card from {base_url}/{path}: {agent_card.name}")
                    return agent_card
                except A2AClientHTTPError as e:
                    if e.status_code == 404:
                        logger.debug(f"Agent card not found at {base_url}/{path}, trying next path")
                        last_404_error = e
                        continue

                    logger.error(f"HTTP error (non-404) fetching from {base_url}/{path}: {e}")
                    if first_non_404_http_error is None:
                        first_non_404_http_error = e
                except (httpx.HTTPError, httpx.TimeoutException) as e:
                    logger.error(f"Transport error fetching from {base_url}/{path}: {e}")
                    if first_transport_error is None:
                        first_transport_error = e
                except Exception as e:
                    logger.error(f"Error parsing or validating card from {base_url}/{path}: {e}")
                    if first_parse_error is None:
                        first_parse_error = e

        if first_non_404_http_error is not None:
            raise A2AAgentCardUpstreamException(
                f"Failed to fetch agent card from {base_url}: {first_non_404_http_error}"
            )

        if first_transport_error is not None:
            raise A2AAgentCardTransportException(f"Failed to fetch agent card from {base_url}: {first_transport_error}")

        if first_parse_error is not None:
            raise A2AAgentCardParseException(f"Failed to parse agent card from {base_url}: {first_parse_error}")

        if last_404_error is not None:
            raise A2AAgentCardNotFoundException(
                f"Agent card not found at {base_url} (tried: {', '.join(well_known_paths)})"
            )

        raise A2AAgentCardUpstreamException(f"Failed to fetch agent card from {base_url} for unknown reason")

    async def _fetch_agent_card_from_url(self, url: str) -> AgentCard:
        """
        Fetch and validate agent card from URL using SDK.

        Args:
            url: Agent endpoint URL

        Returns:
            Validated AgentCard from remote endpoint

        Raises:
            A2AAgentCardNotFoundException: If both known well-known endpoints return 404
            A2AAgentCardTransportException: If transport/network failures occur
            A2AAgentCardUpstreamException: If upstream returns non-404 errors
            A2AAgentCardParseException: If payload cannot be parsed/validated
        """
        try:
            logger.info(f"Fetching agent card from {url} using SDK")
            return await self._resolve_agent_card_with_fallback(base_url=url, timeout_seconds=15.0)
        except (
            A2AAgentCardNotFoundException,
            A2AAgentCardTransportException,
            A2AAgentCardUpstreamException,
            A2AAgentCardParseException,
        ):
            raise
        except Exception as e:
            logger.error(f"Unexpected error fetching agent card from {url}: {e}", exc_info=True)
            raise A2AAgentCardUpstreamException(f"Failed to fetch agent card from {url}: {e}")

    async def list_agents(
        self,
        query: str | None = None,
        status: str | None = None,
        page: int = 1,
        per_page: int = 20,
        accessible_agent_ids: list[str] | None = None,
    ) -> tuple[list[A2AAgent], int]:
        """
        List agents with optional filtering and pagination.

        Args:
            query: Free-text search across name, description, tags, skills
            status: Filter by operational state (active, inactive, error)
            page: Page number (validated by router)
            per_page: Items per page (validated by router)
            accessible_agent_ids: List of agent ID strings accessible to the user (from ACL)

        Returns:
            Tuple of (agents list, total count)
        """
        try:
            # Build query filters
            filters: dict[str, Any] = {}

            # Filter by accessible agent IDs (ACL)
            if accessible_agent_ids is not None:
                object_ids = [PydanticObjectId(aid) for aid in accessible_agent_ids]
                filters["_id"] = {"$in": object_ids}

            # Filter by status if provided
            if status:
                filters["status"] = status

            # Build text search filter if query provided
            if query:
                # Escape regex special characters to prevent regex injection attacks
                escaped_query = re.escape(query)
                # Search across config fields and card fields: title, description, skills
                filters["$or"] = [
                    {"config.title": {"$regex": escaped_query, "$options": "i"}},
                    {"config.description": {"$regex": escaped_query, "$options": "i"}},
                    {"card.name": {"$regex": escaped_query, "$options": "i"}},
                    {"card.description": {"$regex": escaped_query, "$options": "i"}},
                    {"tags": {"$regex": escaped_query, "$options": "i"}},
                    {"card.skills.name": {"$regex": escaped_query, "$options": "i"}},
                    {"card.skills.description": {"$regex": escaped_query, "$options": "i"}},
                ]

            # Get total count
            total = await A2AAgent.find(filters).count()

            # Get paginated results
            skip = (page - 1) * per_page
            agents = await A2AAgent.find(filters).sort("-createdAt").skip(skip).limit(per_page).to_list()

            logger.info(f"Listed {len(agents)} agents (total: {total}, page: {page}, per_page: {per_page})")
            return agents, total

        except Exception as e:
            logger.error(f"Error listing agents: {e}", exc_info=True)
            raise

    async def get_stats(self) -> dict[str, Any]:
        """
        Get agent statistics.

        Returns:
            Statistics dictionary with agent counts and breakdowns
        """
        try:
            # Total counts
            total_agents = await A2AAgent.count()
            enabled_agents = await A2AAgent.find({"isEnabled": True}).count()
            disabled_agents = await A2AAgent.find({"isEnabled": False}).count()

            # Count by status
            status_pipeline = [{"$group": {"_id": "$status", "count": {"$sum": 1}}}]
            status_results = await A2AAgent.aggregate(status_pipeline).to_list()
            by_status = {result["_id"]: result["count"] for result in status_results}

            # Count by transport
            transport_pipeline = [{"$group": {"_id": "$card.preferred_transport", "count": {"$sum": 1}}}]
            transport_results = await A2AAgent.aggregate(transport_pipeline).to_list()
            by_transport = {result["_id"]: result["count"] for result in transport_results}

            # Total skills and average
            skills_pipeline = [
                {"$project": {"num_skills": {"$size": "$card.skills"}}},
                {"$group": {"_id": None, "total_skills": {"$sum": "$num_skills"}}},
            ]
            skills_results = await A2AAgent.aggregate(skills_pipeline).to_list()
            total_skills = skills_results[0]["total_skills"] if skills_results else 0
            average_skills = round(total_skills / total_agents, 1) if total_agents > 0 else 0.0

            stats = {
                "total_agents": total_agents,
                "enabled_agents": enabled_agents,
                "disabled_agents": disabled_agents,
                "by_status": by_status,
                "by_transport": by_transport,
                "total_skills": total_skills,
                "average_skills_per_agent": average_skills,
            }

            logger.info(f"Agent stats: {total_agents} total, {enabled_agents} enabled")
            return stats

        except Exception as e:
            logger.error(f"Error getting agent stats: {e}", exc_info=True)
            raise

    async def get_agent_by_id(self, agent_id: str) -> A2AAgent:
        """
        Get agent by ID.

        Args:
            agent_id: Agent ID

        Returns:
            Agent document

        Raises:
            ValueError: If agent not found or retrieval fails
        """
        try:
            agent = await A2AAgent.get(PydanticObjectId(agent_id))
            if not agent:
                logger.error(f"Agent not found: {agent_id}")
                raise ValueError(f"Agent not found: {agent_id}")
            logger.debug(f"Retrieved agent: {agent.card.name} (ID: {agent_id})")
            return agent
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Error getting agent {agent_id}: {e}", exc_info=True)
            raise

    async def get_agent_by_path(self, path: str) -> A2AAgent | None:
        """
        Get agent by registry path.

        Args:
            path: Registry path (e.g., /deep-intel)

        Returns:
            Agent document, or None if not found
        """
        return await A2AAgent.find_one({"path": path})

    async def create_agent(self, data: AgentCreateRequest, user_id: str) -> A2AAgent:
        """
        Create a new agent. Automatically fetches agent card from provided URL.

        Args:
            data: Agent creation data (path, title, description, url, type)
            user_id: User ID who creates the agent

        Returns:
            Created agent document

        Raises:
            ValueError: If path already exists or validation fails
        """
        try:
            # Validate transport type
            from registry_pkgs.models.a2a_agent import VALID_TRANSPORT_TYPES

            if data.type not in VALID_TRANSPORT_TYPES:
                raise ValueError(
                    f"Invalid transport type '{data.type}'. Must be one of: {', '.join(sorted(VALID_TRANSPORT_TYPES))}"
                )

            # Check if path already exists
            existing = await A2AAgent.find_one({"path": data.path})
            if existing:
                raise ValueError(f"Agent with path '{data.path}' already exists")

            # Fetch agent card from URL using SDK - KEEP ORIGINAL DATA
            logger.info(f"Fetching agent card from URL for new agent: {data.url}")
            agent_card = await self._fetch_agent_card_from_url(str(data.url))

            # DO NOT modify the agent_card - store it as-is
            # Store user-provided information in config field instead
            from registry_pkgs.models.a2a_agent import AgentConfig, WellKnownConfig

            agent_config = AgentConfig(
                title=data.title,
                description=data.description or "",
                url=str(data.url),  # Store user-provided URL in config
                type=data.type,
            )

            # Create agent document with wellKnown config
            agent = A2AAgent(
                path=data.path,
                card=agent_card,  # Original, unmodified card from third-party
                config=agent_config,  # User-provided configuration including URL
                tags=[],  # Initialize as empty list - tags are registry metadata, not derived from skills
                isEnabled=False,  # Default to disabled for safety
                status=STATUS_ACTIVE,
                author=PydanticObjectId(user_id),
                registeredBy=None,
                registeredAt=datetime.now(UTC),
            )

            # Configure wellKnown for future syncs (URL is now in config.url)
            agent.wellKnown = WellKnownConfig(
                enabled=True,
                lastSyncAt=datetime.now(UTC),
                lastSyncStatus="success",
                lastSyncVersion=agent_card.version,
            )

            # Save to database
            await agent.insert()
            logger.info(
                f"Created agent: {agent.config.title} (ID: {agent.id}, path: {agent.path}) with wellKnown sync enabled"
            )
            return agent

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Error creating agent: {e}", exc_info=True)
            raise ValueError(f"Failed to create agent: {str(e)}")

    async def update_agent(self, agent_id: str, data: AgentUpdateRequest) -> A2AAgent:
        """
        Update an existing agent. If URL is updated, automatically fetches new agent card.

        Args:
            agent_id: Agent ID
            data: Agent update data (path, title, description, url, type, enabled - all optional)

        Returns:
            Updated agent document

        Raises:
            ValueError: If agent not found or validation fails
        """
        try:
            agent = await A2AAgent.get(PydanticObjectId(agent_id))
            if not agent:
                raise ValueError(f"Agent not found: {agent_id}")

            # Check what fields are being updated
            update_data = data.model_dump(exclude_unset=True, by_alias=False)

            # Validate transport type if provided
            if "type" in update_data:
                from registry_pkgs.models.a2a_agent import VALID_TRANSPORT_TYPES

                if update_data["type"] not in VALID_TRANSPORT_TYPES:
                    raise ValueError(
                        f"Invalid transport type '{update_data['type']}'. Must be one of: {', '.join(sorted(VALID_TRANSPORT_TYPES))}"
                    )

            # If URL is being updated, fetch new agent card
            # Only fetch if URL actually changed (compare with config.url to avoid unnecessary fetches)
            if "url" in update_data and update_data["url"]:
                new_url = str(update_data["url"])
                current_url = str(agent.config.url) if agent.config and agent.config.url else None

                # Normalize URLs for comparison (remove trailing slashes)
                new_url_normalized = new_url.rstrip("/")
                current_url_normalized = current_url.rstrip("/") if current_url else None

                if new_url_normalized != current_url_normalized:
                    logger.info(f"URL changed from {current_url} to {new_url}, fetching new agent card")

                    # Fetch new agent card from URL - KEEP ORIGINAL DATA
                    agent_card = await self._fetch_agent_card_from_url(new_url)

                    # DO NOT modify the agent_card - store it as-is
                    agent.card = agent_card

                    # Update config.url with user-provided URL
                    # Ensure config exists for backward compatibility with old data
                    if not agent.config:
                        from registry_pkgs.models.a2a_agent import AgentConfig

                        agent.config = AgentConfig(
                            title=agent.card.name,
                            description=agent.card.description,
                            url=new_url,
                            type="jsonrpc",  # Default type
                        )
                    else:
                        agent.config.url = new_url

                    # Update wellKnown sync status (URL is in config.url now)
                    from registry_pkgs.models.a2a_agent import WellKnownConfig

                    if not agent.wellKnown:
                        agent.wellKnown = WellKnownConfig(
                            enabled=True,
                            lastSyncAt=datetime.now(UTC),
                            lastSyncStatus="success",
                            lastSyncVersion=agent_card.version,
                        )
                    else:
                        agent.wellKnown.enabled = True
                        agent.wellKnown.lastSyncAt = datetime.now(UTC)
                        agent.wellKnown.lastSyncStatus = "success"
                        agent.wellKnown.lastSyncVersion = agent_card.version
                else:
                    logger.debug(f"URL unchanged ({new_url}), skipping agent card fetch")

            # Update config fields (title, description, type)
            # These are stored separately in the config field
            if "title" in update_data or "description" in update_data or "type" in update_data:
                # Ensure config exists for backward compatibility with old data
                if not agent.config:
                    from registry_pkgs.models.a2a_agent import AgentConfig

                    agent.config = AgentConfig(
                        title=update_data.get("title", agent.card.name),
                        description=update_data.get("description", agent.card.description),
                        url=str(agent.card.url) if agent.card.url else None,
                        type=update_data.get("type", "jsonrpc"),
                    )
                else:
                    if "title" in update_data:
                        agent.config.title = update_data["title"]
                    if "description" in update_data:
                        agent.config.description = update_data["description"]
                    if "type" in update_data:
                        agent.config.type = update_data["type"]

            # Update path if provided
            if "path" in update_data:
                # Check if new path conflicts with existing agent
                existing = await A2AAgent.find_one({"path": update_data["path"], "_id": {"$ne": agent.id}})
                if existing:
                    raise ValueError(f"Agent with path '{update_data['path']}' already exists")
                agent.path = update_data["path"]

            # Update enabled state if provided
            if "enabled" in update_data:
                agent.isEnabled = update_data["enabled"]

            # Update timestamp
            agent.updatedAt = datetime.now(UTC)

            # Save changes
            await agent.save()
            logger.info(f"Updated agent: {agent.config.title} (ID: {agent_id})")
            return agent

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Error updating agent {agent_id}: {e}", exc_info=True)
            raise ValueError(f"Failed to update agent: {str(e)}")

    async def delete_agent(self, agent_id: str) -> bool:
        """
        Delete an agent.

        Args:
            agent_id: Agent ID

        Returns:
            True if deleted successfully

        Raises:
            ValueError: If agent not found
        """
        try:
            agent = await A2AAgent.get(PydanticObjectId(agent_id))
            if not agent:
                raise ValueError(f"Agent not found: {agent_id}")

            await agent.delete()
            logger.info(f"Deleted agent: {agent.card.name} (ID: {agent_id})")
            return True

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Error deleting agent {agent_id}: {e}", exc_info=True)
            raise ValueError(f"Failed to delete agent: {str(e)}")

    async def toggle_agent_status(self, agent_id: str, enabled: bool) -> A2AAgent:
        """
        Toggle agent enabled/disabled status.

        Args:
            agent_id: Agent ID
            enabled: New enabled state

        Returns:
            Updated agent document

        Raises:
            ValueError: If agent not found
        """
        try:
            agent = await A2AAgent.get(PydanticObjectId(agent_id))
            if not agent:
                raise ValueError(f"Agent not found: {agent_id}")

            agent.isEnabled = enabled
            agent.updatedAt = datetime.now(UTC)
            await agent.save()

            logger.info(f"Toggled agent {agent.card.name} to {'enabled' if enabled else 'disabled'}")
            return agent

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Error toggling agent {agent_id}: {e}", exc_info=True)
            raise ValueError(f"Failed to toggle agent: {str(e)}")

    async def sync_wellknown(self, agent_id: str) -> dict[str, Any]:
        """
        Sync agent configuration from .well-known/agent-card.json endpoint using SDK.

        Args:
            agent_id: Agent ID

        Returns:
            Sync result with status and changes

        Raises:
            ValueError: If agent not found, well-known not enabled, or sync fails
        """
        agent: A2AAgent | None = None

        try:
            agent = await A2AAgent.get(PydanticObjectId(agent_id))
            if not agent:
                raise ValueError(f"Agent not found: {agent_id}")

            # Check if well-known is enabled
            if not agent.wellKnown or not agent.wellKnown.enabled:
                raise ValueError("Well-known sync is not enabled for this agent")

            # URL comes from config.url (user-provided), fallback to card.url for backward compatibility
            if agent.config and agent.config.url:
                agent_url = str(agent.config.url)
            elif agent.card and agent.card.url:
                agent_url = str(agent.card.url)
                logger.warning(
                    f"Agent {agent_id} missing config.url, falling back to card.url. Consider updating the agent."
                )
            else:
                raise ValueError("Agent URL is not configured")

            # Use shared fallback helper for deterministic exception semantics.
            logger.info(f"Fetching agent card from {agent_url} using SDK")
            base_url = agent_url.rsplit("/.well-known", 1)[0]
            updated_card = await self._resolve_agent_card_with_fallback(base_url=base_url, timeout_seconds=10.0)

            # Track changes
            changes = []
            old_card = agent.card

            # Compare versions
            if old_card.version != updated_card.version:
                changes.append(f"Version: {old_card.version} → {updated_card.version}")

            # Compare descriptions
            if old_card.description != updated_card.description:
                changes.append("Updated description")

            # Compare skills count
            if len(old_card.skills or []) != len(updated_card.skills or []):
                changes.append(f"Skills count: {len(old_card.skills or [])} → {len(updated_card.skills or [])}")

            # Compare capabilities
            if old_card.capabilities != updated_card.capabilities:
                changes.append("Updated capabilities")

            # Update agent card with SDK-validated card (DO NOT modify - keep original)
            agent.card = updated_card

            # Update well-known sync metadata
            agent.wellKnown.lastSyncAt = datetime.now(UTC)
            agent.wellKnown.lastSyncStatus = "success"
            agent.wellKnown.lastSyncVersion = updated_card.version
            agent.wellKnown.syncError = None

            # Update timestamp
            agent.updatedAt = datetime.now(UTC)

            # Save changes
            await agent.save()

            logger.info(f"Successfully synced agent {agent.card.name} from well-known: {len(changes)} changes")

            return {
                "message": "Well-known configuration synced successfully",
                "sync_status": "success",
                "synced_at": agent.wellKnown.lastSyncAt,
                "version": updated_card.version,
                "changes": changes if changes else ["No changes detected"],
            }

        except A2AAgentCardTransportException as e:
            # Update sync error status
            if agent and agent.wellKnown:
                agent.wellKnown.lastSyncStatus = "failed"
                agent.wellKnown.syncError = f"HTTP error: {str(e)}"
                await agent.save()

            logger.error(f"HTTP error syncing agent {agent_id}: {e}", exc_info=True)
            raise

        except (A2AAgentCardNotFoundException, A2AAgentCardUpstreamException, A2AAgentCardParseException) as e:
            if agent and agent.wellKnown:
                agent.wellKnown.lastSyncStatus = "failed"
                agent.wellKnown.syncError = str(e)
                await agent.save()

            logger.error(f"Error syncing agent {agent_id}: {e}", exc_info=True)
            raise

        except ValueError:
            raise
        except Exception as e:
            # Update sync error status
            if agent and agent.wellKnown:
                agent.wellKnown.lastSyncStatus = "failed"
                agent.wellKnown.syncError = str(e)
                await agent.save()

            logger.error(f"Error syncing agent {agent_id}: {e}", exc_info=True)
            raise ValueError(f"Failed to sync well-known configuration: {str(e)}")
