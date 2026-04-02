"""
MongoDB ODM Schema for A2A (Agent-to-Agent) Agents

This module defines the MongoDB Document schema for A2A agents using the official a2a-sdk.
The SDK handles all A2A protocol validation and compliance.

Storage Structure:
{
  "_id": ObjectId("..."),

  # Registry-specific Fields
  "path": "/deep-intel",  # Registry path (not part of SDK AgentCard)

  # A2A Protocol Card (validated by SDK)
  "card": {
    "name": "Deep Intel Agent",
    "description": "Orchestrates AWS research and BI into full report",
    "url": "https://strandsagents.com/agents/deep-intel",
    "version": "0.1.0",
    "protocolVersion": "1.0",
    "capabilities": {"streaming": true},
    "skills": [...],
    "securitySchemes": {...},
    "preferredTransport": "HTTP+JSON",
    "defaultInputModes": ["text/plain"],
    "defaultOutputModes": ["application/json"],
    "provider": {"organization": "Strands AI", "url": "https://..."}
  },

  # Registry Metadata
  "tags": ["ai", "research"],
  "status": "active",
  "isEnabled": true,

  # Well-known Configuration
  "wellKnown": {
    "enabled": true,
    "url": "https://strandsagents.com/.well-known/agent-card.json",
    "lastSyncAt": ISODate("2024-01-20T12:00:00Z"),
    "lastSyncStatus": "success"
  },

  # Access Control
  "author": ObjectId("..."),
  "registeredBy": "john.doe@example.com",
  "registeredAt": ISODate("2024-01-15T10:30:00Z"),
  "createdAt": ISODate("2024-01-15T10:30:00Z"),
  "updatedAt": ISODate("2024-01-20T15:45:00Z")
}
"""

import logging
from datetime import UTC, datetime
from typing import Any, ClassVar

from a2a.types import AgentCard
from beanie import Document, Insert, PydanticObjectId, Replace, Save, before_event
from langchain_core.documents import Document as LangChainDocument
from pydantic import BaseModel, ConfigDict, Field, HttpUrl
from pymongo import IndexModel

logger = logging.getLogger(__name__)

# ========== Constants ==========

# Registry Status Values
STATUS_ACTIVE = "active"
STATUS_INACTIVE = "inactive"
STATUS_ERROR = "error"
VALID_STATUSES: set[str] = {STATUS_ACTIVE, STATUS_INACTIVE, STATUS_ERROR}


# ========== Registry-Specific Models ==========


class WellKnownConfig(BaseModel):
    """Manual .well-known sync configuration."""

    enabled: bool = Field(default=False, description="Whether well-known sync is enabled")
    url: HttpUrl | None = Field(None, description="URL to .well-known/agent-card.json")

    # Sync metadata (manual refresh only)
    lastSyncAt: datetime | None = Field(None, description="Last successful sync timestamp")
    lastSyncStatus: str | None = Field(None, description="success | failed | unreachable")
    lastSyncVersion: str | None = Field(None, description="Agent version from last sync")
    syncError: str | None = Field(None, description="Error message from last sync attempt")

    model_config = ConfigDict(populate_by_name=True)


# ========== Main Document ==========


class A2AAgent(Document):
    """
    MongoDB Document for A2A Agents using official a2a-sdk.

    This model wraps the SDK's AgentCard with registry-specific metadata.
    The SDK handles all A2A protocol validation and compliance.

    Design Principles:
    - SDK-powered: Uses a2a-sdk's AgentCard for protocol compliance
    - Registry aware: Adds registry-specific metadata (tags, status, etc.)
    - Well-known support: Manual sync configuration for .well-known endpoints
    - ACL integration: Uses author field for ownership and ACLService for permissions

    Access Control:
    - Agent creation automatically creates ACL entry granting creator OWNER permissions
    - Update/Delete operations require appropriate ACL permissions (EDIT/DELETE)
    - Query operations filter based on ACL visibility (VIEW permission)
    - Permissions managed via ACLService using ResourceType.REMOTE_AGENT
    """

    # ========== Registry-specific Fields ==========
    path: str = Field(..., description="Registry path (e.g., /deep-intel)")

    # ========== A2A Protocol Card (SDK) ==========
    card: AgentCard = Field(description="A2A protocol-compliant agent card (validated by SDK)")

    # ========== Registry Metadata ==========
    tags: list[str] = Field(default_factory=list, description="Registry categorization tags")
    status: str = Field(default=STATUS_ACTIVE, description="Operational state: active, inactive, error")
    isEnabled: bool = Field(default=False, description="Whether agent is enabled in registry")

    # ========== Well-known Configuration ==========
    wellKnown: WellKnownConfig | None = Field(None, description="Manual .well-known sync configuration")

    # ========== Audit Trail & Access Control ==========
    author: PydanticObjectId = Field(description="User who created/registered this agent (for ACL)")
    registeredBy: str | None = Field(None, description="Username or service account who registered")
    registeredAt: datetime | None = Field(None, description="Registration timestamp")
    createdAt: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Creation timestamp")
    updatedAt: datetime = Field(default_factory=lambda: datetime.now(UTC), description="Last update timestamp")

    federationRefId: PydanticObjectId | None = None
    federationMetadata: dict[str, Any] | None = None

    # ========== Settings ==========
    class Settings:
        name = "a2a_agents"
        use_state_management = True
        keep_nulls = False

        # Indexes for efficient queries
        indexes = [
            IndexModel([("path", 1)], unique=True),
            "tags",
            "isEnabled",
            "status",
            [("card.name", "text")],
            [("author", 1)],
            [("registeredBy", 1)],
            IndexModel([("federationRefId", 1)]),
            IndexModel([("federationMetadata.runtimeArn", 1)], sparse=True),
        ]

    # ========== Lifecycle Hooks ==========
    @before_event(Insert, Replace, Save)
    async def update_timestamps(self):
        """Update timestamps before saving."""
        self.updatedAt = datetime.now(UTC)
        if not self.createdAt:
            self.createdAt = datetime.now(UTC)

    # ========== Vector Search Integration ==========
    COLLECTION_NAME: ClassVar[str] = "A2a_agents"

    def to_searchable_text(self) -> str:
        """
        Generate searchable text for vector embedding.

        Returns:
            Combined text representation of agent for embedding
        """
        parts = [
            f"Name: {self.card.name}",
            f"Description: {self.card.description}",
            f"Path: {self.path}",
        ]

        # Add skill information
        if self.card.skills:
            skills_text = "\n".join(
                [
                    f"Skill {i + 1}: {skill.name} - {skill.description} (Tags: {', '.join(skill.tags or [])})"
                    for i, skill in enumerate(self.card.skills)
                ]
            )
            parts.append(f"Skills:\n{skills_text}")

        # Add tags
        if self.tags:
            parts.append(f"Tags: {', '.join(self.tags)}")

        # Add provider info
        if self.card.provider:
            parts.append(f"Provider: {self.card.provider.organization}")

        return "\n".join(parts)

    def to_documents(self) -> list[LangChainDocument]:
        """
        Convert A2AAgent to vector documents.

        Emits:
        - 1 agent overview document
        - N skill documents (one per skill)
        """
        agent_id = str(self.id) if self.id else None
        base_metadata = {
            "collection": self.COLLECTION_NAME,
            "agent_id": agent_id,
            "agent_name": self.card.name,
            "path": self.path,
            "status": self.status,
            "is_enabled": self.isEnabled,
            "tags": self.tags,
        }
        # Federation metadata lets vector sync target one federated A2A runtime precisely.
        if self.federationRefId is not None:
            base_metadata["federation_id"] = str(self.federationRefId)
        runtime_version = (self.federationMetadata or {}).get("runtimeVersion")
        if runtime_version is not None:
            base_metadata["runtimeVersion"] = str(runtime_version)
        # Keep runtimeArn for debugging and future runtime-scoped repair.
        runtime_arn = (self.federationMetadata or {}).get("runtimeArn")
        if runtime_arn:
            base_metadata["runtimeArn"] = runtime_arn

        docs: list[LangChainDocument] = [
            LangChainDocument(
                page_content=self.to_searchable_text(),
                metadata={
                    **base_metadata,
                    "entity_type": "agent",
                },
            )
        ]

        for skill in self.card.skills or []:
            skill_name = getattr(skill, "name", "") or ""
            skill_desc = getattr(skill, "description", "") or ""
            skill_tags = getattr(skill, "tags", None) or []
            content = (
                f"Agent: {self.card.name}\n"
                f"Skill: {skill_name}\n"
                f"Description: {skill_desc}\n"
                f"Tags: {', '.join(skill_tags)}"
            )
            docs.append(
                LangChainDocument(
                    page_content=content,
                    metadata={
                        **base_metadata,
                        "entity_type": "skill",
                        "skill_name": skill_name,
                    },
                )
            )

        return docs

    @classmethod
    def from_document(cls, document: LangChainDocument) -> dict[str, Any]:
        """
        Extract minimal metadata from vector document.
        """
        metadata = document.metadata or {}
        return {
            "agent_id": metadata.get("agent_id"),
            "agent_name": metadata.get("agent_name"),
            "path": metadata.get("path"),
            "entity_type": metadata.get("entity_type"),
            "skill_name": metadata.get("skill_name"),
            "status": metadata.get("status"),
            "is_enabled": metadata.get("is_enabled"),
            "content": document.page_content,
        }

    def is_accessible_by_user(self, username: str, user_groups: list[str], is_admin: bool = False) -> bool:
        """
        DEPRECATED: Access control is now handled by ACL permissions.
        Use ACLService.check_user_permission() instead.

        This method is kept for backward compatibility and always returns True.
        """
        logger.warning(
            "is_accessible_by_user() is deprecated. Use ACLService.check_user_permission() for access control."
        )
        return True

    def to_a2a_agent_card(self) -> dict[str, Any]:
        """
        Export A2A-compliant agent card (without registry metadata).

        The SDK's AgentCard.model_dump() provides the standard card format.

        Returns:
            Standard A2A agent card as dictionary
        """
        return self.card.model_dump(mode="json", by_alias=True, exclude_none=True)

    @classmethod
    def from_a2a_agent_card(cls, card_data: dict[str, Any], path: str, **registry_fields) -> "A2AAgent":
        """
        Create A2AAgent from standard A2A agent card using SDK validation.

        Args:
            card_data: A2A agent card dictionary (without path - SDK doesn't support it)
            path: Registry path (required, e.g., /deep-intel)
            **registry_fields: Additional registry metadata such as:
                - author: User ID who created this agent (required for ACL)
                - isEnabled: Enabled state (default: False)
                - registeredBy: Username or service account
                - tags: List of tags

        Returns:
            A2AAgent instance

        Raises:
            ValueError: If validation fails or author/path field is missing
        """
        # Validate required registry fields
        if "author" not in registry_fields:
            raise ValueError("'author' field is required in registry_fields for ACL integration")

        if not path:
            raise ValueError("'path' is required for registry agent")

        # Remove path from card_data if it exists (SDK doesn't support it)
        card_data_clean = {k: v for k, v in card_data.items() if k != "path"}

        # SDK validates the entire card structure
        try:
            agent_card = AgentCard(**card_data_clean)
        except Exception as e:
            raise ValueError(f"Invalid A2A agent card: {str(e)}")

        # Create MongoDB document
        return cls(
            path=path,
            card=agent_card,
            author=registry_fields["author"],
            isEnabled=registry_fields.get("isEnabled", False),
            tags=registry_fields.get("tags", []),
            status=registry_fields.get("status", STATUS_ACTIVE),
            registeredBy=registry_fields.get("registeredBy"),
            registeredAt=registry_fields.get("registeredAt"),
            wellKnown=registry_fields.get("wellKnown"),
            federationRefId=registry_fields.get("federationRefId"),
            federationMetadata=registry_fields.get("federationMetadata"),
        )

    # ========== Pydantic Configuration ==========
    model_config = ConfigDict(
        json_encoders={
            datetime: lambda v: v.isoformat() if v else None,
            HttpUrl: str,
        },
        populate_by_name=True,
        use_enum_values=True,
    )

    # ========== Special Methods ==========
    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"A2AAgent(id={self.id}, path='{self.path}', name='{self.card.name}', "
            f"version='{self.card.version}', isEnabled={self.isEnabled})"
        )

    def __str__(self) -> str:
        """Human-readable string representation."""
        return f"{self.card.name} v{self.card.version} ({self.path})"


# ========== Exports for Backward Compatibility ==========
# Export SDK types directly for use in other modules

from a2a.types import AgentProvider, AgentSkill

__all__ = [
    "A2AAgent",
    "AgentCard",
    "AgentSkill",
    "AgentProvider",
    "WellKnownConfig",
    "STATUS_ACTIVE",
    "STATUS_INACTIVE",
    "STATUS_ERROR",
    "VALID_STATUSES",
]
