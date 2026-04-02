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

from registry_pkgs.models.a2a_agent import STATUS_ACTIVE, A2AAgent

from ..schemas.a2a_agent_api_schemas import AgentCreateRequest, AgentUpdateRequest

logger = logging.getLogger(__name__)


class A2AAgentService:
    """Service for A2A Agent operations"""

    async def _fetch_agent_card_from_url(self, url: str) -> AgentCard:
        """
        Fetch and validate agent card from URL using SDK.

        Args:
            url: Agent endpoint URL

        Returns:
            Validated AgentCard from remote endpoint

        Raises:
            ValueError: If fetching or validation fails
        """
        try:
            logger.info(f"Fetching agent card from {url} using SDK")

            timeout = httpx.Timeout(15.0)
            agent_card = None
            last_error = None

            # Try multiple well-known paths (agent-card.json first, then agent.json)
            well_known_paths = [".well-known/agent-card.json", ".well-known/agent.json"]

            async with httpx.AsyncClient(timeout=timeout) as client:
                for path in well_known_paths:
                    try:
                        resolver = A2ACardResolver(
                            base_url=url,
                            httpx_client=client,
                            agent_card_path=path,
                        )
                        agent_card = await resolver.get_agent_card()
                        logger.info(f"Successfully fetched agent card from {url}/{path}: {agent_card.name}")
                        break
                    except A2AClientHTTPError as e:
                        if e.status_code == 404:
                            logger.debug(f"Agent card not found at {url}/{path}, trying next path")
                            last_error = e
                            continue
                        else:
                            logger.error(f"HTTP error (non-404) fetching from {url}/{path}: {e}")
                            raise ValueError(f"Failed to fetch agent card from {url}/{path}: {str(e)}")
                    except Exception as e:
                        logger.debug(f"Error fetching from {url}/{path}: {e}")
                        last_error = e
                        continue

            if not agent_card:
                error_msg = f"Failed to fetch agent card from {url} (tried: {', '.join(well_known_paths)})"
                if last_error:
                    error_msg += f". Last error: {str(last_error)}"
                logger.error(error_msg)
                raise ValueError(error_msg)

            return agent_card

        except ValueError:
            raise
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            logger.error(f"HTTP error fetching agent card from {url}: {e}", exc_info=True)
            raise ValueError(f"Failed to fetch agent card from {url}: {str(e)}")
        except Exception as e:
            logger.error(f"Error fetching agent card from {url}: {e}", exc_info=True)
            raise ValueError(f"Failed to fetch agent card from {url}: {str(e)}")

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
                # Search across card fields: name, description, skills
                filters["$or"] = [
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

    async def create_agent(self, data: AgentCreateRequest, user_id: str) -> A2AAgent:
        """
        Create a new agent. Automatically fetches agent card from provided URL.

        Args:
            data: Agent creation data (path, name, description, url)
            user_id: User ID who creates the agent

        Returns:
            Created agent document

        Raises:
            ValueError: If path already exists or validation fails
        """
        try:
            # Check if path already exists
            existing = await A2AAgent.find_one({"path": data.path})
            if existing:
                raise ValueError(f"Agent with path '{data.path}' already exists")

            # Fetch agent card from URL using SDK
            logger.info(f"Fetching agent card from URL for new agent: {data.url}")
            agent_card = await self._fetch_agent_card_from_url(str(data.url))

            # Override name and description from request if provided
            # This allows user to customize these fields in registry
            card_data = agent_card.model_dump(by_alias=False)
            card_data["name"] = data.name
            card_data["description"] = data.description or agent_card.description
            card_data["url"] = str(data.url)  # Ensure URL matches the request

            # Recreate agent card with overridden values
            agent_card = AgentCard(**card_data)

            # Create agent document with wellKnown config
            agent = A2AAgent(
                path=data.path,
                card=agent_card,
                tags=[],  # Initialize as empty list - tags are registry metadata, not derived from skills
                isEnabled=False,  # Default to disabled for safety
                status=STATUS_ACTIVE,
                author=PydanticObjectId(user_id),
                registeredBy=None,
                registeredAt=datetime.now(UTC),
            )

            # Configure wellKnown for future syncs
            from registry_pkgs.models.a2a_agent import WellKnownConfig

            agent.wellKnown = WellKnownConfig(
                enabled=True,
                url=str(data.url),
                lastSyncAt=datetime.now(UTC),
                lastSyncStatus="success",
                lastSyncVersion=agent_card.version,
            )

            # Save to database
            await agent.insert()
            logger.info(
                f"Created agent: {agent.card.name} (ID: {agent.id}, path: {agent.path}) with wellKnown sync enabled"
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
            data: Agent update data (path, name, description, url - all optional)

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

            # If URL is being updated, fetch new agent card
            if "url" in update_data and update_data["url"]:
                new_url = str(update_data["url"])
                logger.info(f"URL changed, fetching new agent card from {new_url}")

                # Fetch new agent card from URL
                agent_card = await self._fetch_agent_card_from_url(new_url)

                # Override with request fields if provided
                card_data = agent_card.model_dump(by_alias=False)
                card_data["url"] = new_url

                if "name" in update_data:
                    card_data["name"] = update_data["name"]
                if "description" in update_data:
                    card_data["description"] = update_data["description"]

                # Recreate agent card
                agent.card = AgentCard(**card_data)

                # Note: tags are not updated from skills - they are separate registry metadata
                # agent.tags remains unchanged during URL update

                # Update wellKnown configuration
                from registry_pkgs.models.a2a_agent import WellKnownConfig

                if not agent.wellKnown:
                    agent.wellKnown = WellKnownConfig(
                        enabled=True,
                        url=new_url,
                    )
                else:
                    agent.wellKnown.url = new_url
                    agent.wellKnown.enabled = True

                agent.wellKnown.lastSyncAt = datetime.now(UTC)
                agent.wellKnown.lastSyncStatus = "success"
                agent.wellKnown.lastSyncVersion = agent.card.version

            else:
                # Only update name/description without fetching card
                # This allows minor tweaks without re-fetching
                card_data = agent.card.model_dump(by_alias=False)

                if "name" in update_data:
                    card_data["name"] = update_data["name"]
                if "description" in update_data:
                    card_data["description"] = update_data["description"]

                # Recreate agent card with updated fields
                agent.card = AgentCard(**card_data)

            # Update path if provided
            if "path" in update_data:
                # Check if new path conflicts with existing agent
                existing = await A2AAgent.find_one({"path": update_data["path"], "_id": {"$ne": agent.id}})
                if existing:
                    raise ValueError(f"Agent with path '{update_data['path']}' already exists")
                agent.path = update_data["path"]

            # Update timestamp
            agent.updatedAt = datetime.now(UTC)

            # Save changes
            await agent.save()
            logger.info(f"Updated agent: {agent.card.name} (ID: {agent_id})")
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
        try:
            agent = await A2AAgent.get(PydanticObjectId(agent_id))
            if not agent:
                raise ValueError(f"Agent not found: {agent_id}")

            # Check if well-known is enabled
            if not agent.wellKnown or not agent.wellKnown.enabled:
                raise ValueError("Well-known sync is not enabled for this agent")

            if not agent.wellKnown.url:
                raise ValueError("Well-known URL is not configured")

            # Use SDK to fetch and validate agent card with fallback
            logger.info(f"Fetching agent card from {agent.wellKnown.url} using SDK")

            timeout = httpx.Timeout(10.0)
            updated_card = None
            last_error = None
            base_url = str(agent.wellKnown.url).rsplit("/.well-known", 1)[0]

            # Try multiple well-known paths (agent-card.json first, then agent.json)
            well_known_paths = [".well-known/agent-card.json", ".well-known/agent.json"]

            async with httpx.AsyncClient(timeout=timeout) as client:
                for path in well_known_paths:
                    try:
                        resolver = A2ACardResolver(
                            base_url=base_url,
                            httpx_client=client,
                            agent_card_path=path,
                        )
                        updated_card = await resolver.get_agent_card()
                        logger.info(f"Successfully fetched agent card from {base_url}/{path}")
                        break
                    except A2AClientHTTPError as e:
                        if e.status_code == 404:
                            logger.debug(f"Agent card not found at {base_url}/{path}, trying next path")
                            last_error = e
                            continue
                        else:
                            logger.error(f"HTTP error (non-404) fetching from {base_url}/{path}: {e}")
                            raise ValueError(f"Failed to sync agent card from {base_url}/{path}: {str(e)}")
                    except Exception as e:
                        logger.debug(f"Error fetching from {base_url}/{path}: {e}")
                        last_error = e
                        continue

            if not updated_card:
                error_msg = f"Failed to fetch agent card from {base_url} (tried: {', '.join(well_known_paths)})"
                if last_error:
                    error_msg += f". Last error: {str(last_error)}"
                logger.error(error_msg)
                raise ValueError(error_msg)

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

            # Update agent card with SDK-validated card
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

        except (httpx.HTTPError, httpx.TimeoutException) as e:
            # Update sync error status
            if agent and agent.wellKnown:
                agent.wellKnown.lastSyncStatus = "failed"
                agent.wellKnown.syncError = f"HTTP error: {str(e)}"
                await agent.save()

            logger.error(f"HTTP error syncing agent {agent_id}: {e}", exc_info=True)
            raise ValueError(f"Failed to fetch agent card: {str(e)}")

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
