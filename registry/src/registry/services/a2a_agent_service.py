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
from a2a.client import A2ACardResolver
from a2a.types import AgentCard
from beanie import PydanticObjectId

from registry_pkgs.models.a2a_agent import STATUS_ACTIVE, A2AAgent

from ..schemas.a2a_agent_api_schemas import AgentCreateRequest, AgentUpdateRequest

logger = logging.getLogger(__name__)


class A2AAgentService:
    """Service for A2A Agent operations"""

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
        Create a new agent using SDK for validation.

        Args:
            data: Agent creation data
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

            # Build agent card data for SDK validation (path is NOT part of SDK AgentCard)
            card_data = {
                "name": data.name,
                "description": data.description,
                "url": str(data.url),
                "version": data.version,
                "protocolVersion": data.protocolVersion,
                "capabilities": data.capabilities,
                "skills": [skill.model_dump(by_alias=True, exclude_none=True) for skill in data.skills],
                "securitySchemes": data.securitySchemes,
                "preferredTransport": data.preferredTransport,
                "defaultInputModes": data.defaultInputModes,
                "defaultOutputModes": data.defaultOutputModes,
            }

            # Add optional provider
            if data.provider:
                card_data["provider"] = {
                    "organization": data.provider.organization,
                    "url": data.provider.url,
                }

            # SDK validates the entire card structure
            try:
                agent_card = AgentCard(**card_data)
            except Exception as e:
                raise ValueError(f"Invalid agent card: {str(e)}")

            # Create agent document (path is registry-specific, not in SDK card)
            agent = A2AAgent(
                path=data.path,
                card=agent_card,
                tags=data.tags,
                isEnabled=data.enabled,
                status=STATUS_ACTIVE,
                author=PydanticObjectId(user_id),
                registeredBy=None,
                registeredAt=datetime.now(UTC),
            )

            # Save to database
            await agent.insert()
            logger.info(f"Created agent: {agent.card.name} (ID: {agent.id}, path: {agent.path})")
            return agent

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Error creating agent: {e}", exc_info=True)
            raise ValueError(f"Failed to create agent: {str(e)}")

    async def update_agent(self, agent_id: str, data: AgentUpdateRequest) -> A2AAgent:
        """
        Update an existing agent.

        Args:
            agent_id: Agent ID
            data: Agent update data (partial)

        Returns:
            Updated agent document

        Raises:
            ValueError: If agent not found or validation fails
        """
        try:
            agent = await A2AAgent.get(PydanticObjectId(agent_id))
            if not agent:
                raise ValueError(f"Agent not found: {agent_id}")

            # Get current card as dict
            card_data = agent.card.model_dump(by_alias=False)

            # Update card fields if provided
            update_data = data.model_dump(exclude_unset=True, by_alias=False)

            # Map request fields to card fields
            card_field_mapping = {
                "name": "name",
                "description": "description",
                "version": "version",
                "capabilities": "capabilities",
                "securitySchemes": "securitySchemes",
                "preferredTransport": "preferredTransport",
                "defaultInputModes": "defaultInputModes",
                "defaultOutputModes": "defaultOutputModes",
            }

            # Update card fields
            for request_field, card_field in card_field_mapping.items():
                if request_field in update_data:
                    card_data[card_field] = update_data[request_field]

            # Handle skills update
            if "skills" in update_data and update_data["skills"] is not None:
                card_data["skills"] = [skill.model_dump(by_alias=True, exclude_none=True) for skill in data.skills]

            # Handle provider update
            if "provider" in update_data and update_data["provider"] is not None:
                card_data["provider"] = {
                    "organization": data.provider.organization,
                    "url": data.provider.url,
                }

            # Validate updated card with SDK
            try:
                agent.card = AgentCard(**card_data)
            except Exception as e:
                raise ValueError(f"Invalid agent card update: {str(e)}")

            # Update registry-specific fields
            if "tags" in update_data:
                agent.tags = update_data["tags"]

            if "enabled" in update_data:
                agent.isEnabled = update_data["enabled"]

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

            # Use SDK to fetch and validate agent card
            logger.info(f"Fetching agent card from {agent.wellKnown.url} using SDK")

            timeout = httpx.Timeout(10.0)

            async with httpx.AsyncClient(timeout=timeout) as client:
                resolver = A2ACardResolver(
                    base_url=str(agent.wellKnown.url).rsplit("/.well-known", 1)[0],
                    httpx_client=client,
                )
                # SDK handles fetching, parsing, and validation
                updated_card = await resolver.get_agent_card()

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


# Singleton instance
a2a_agent_service = A2AAgentService()
