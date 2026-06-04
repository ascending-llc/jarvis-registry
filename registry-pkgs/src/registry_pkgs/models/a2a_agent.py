"""
MongoDB ODM Schema for A2A (Agent-to-Agent) Agents

This module defines the MongoDB Document schema for A2A agents using the official a2a-sdk.
The SDK handles all A2A protocol validation and compliance.

Storage Structure:
{
  "_id": ObjectId("..."),

  # Registry-specific Fields
  "path": "deep-intel",  # Registry path in slug format (no slashes), used in /proxy/a2a/{path}

  # A2A Protocol Card (validated by SDK - ORIGINAL DATA, DO NOT MODIFY)
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

  # Registry-specific Configuration (user-provided metadata)
  "config": {
    "title": "My Custom Agent Name",  # User-provided display title
    "description": "My custom description",  # User-provided description
    "url": "https://strandsagents.com/agents/deep-intel",  # User-provided agent URL (where card was fetched)
    "type": "jsonrpc"  # Transport type: jsonrpc, grpc, http_json
  },

  # Registry Metadata
  "tags": ["ai", "research"],
  "status": "active",
  "isEnabled": true,

  # Well-known Configuration (sync state only, URL is in config.url)
  "wellKnown": {
    "enabled": true,
    "lastSyncAt": ISODate("2024-01-20T12:00:00Z"),
    "lastSyncStatus": "success",
    "lastSyncVersion": "0.1.0"
  },

  # Access Control
  "author": ObjectId("..."),
  "registeredBy": "john.doe@example.com",
  "registeredAt": ISODate("2024-01-15T10:30:00Z"),
  "createdAt": ISODate("2024-01-15T10:30:00Z"),
  "updatedAt": ISODate("2024-01-20T15:45:00Z")
}
"""

import hashlib
import logging
import re
from datetime import UTC, datetime
from typing import Any, ClassVar

from a2a.types import AgentCard
from beanie import Document, Insert, PydanticObjectId, Replace, Save, SaveChanges, Update, before_event
from langchain_core.documents import Document as LangChainDocument
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator
from pymongo import IndexModel

from .enums import A2AEntityType
from .federation import AgentCoreRuntimeAccessConfig

logger = logging.getLogger(__name__)

# ========== Constants ==========

# Registry Status Values
STATUS_ACTIVE = "active"
STATUS_INACTIVE = "inactive"
STATUS_ERROR = "error"
VALID_STATUSES: set[str] = {STATUS_ACTIVE, STATUS_INACTIVE, STATUS_ERROR}

# Transport Type Values
TRANSPORT_JSONRPC = "jsonrpc"
TRANSPORT_GRPC = "grpc"
TRANSPORT_HTTP_JSON = "http_json"
VALID_TRANSPORT_TYPES: set[str] = {TRANSPORT_JSONRPC, TRANSPORT_GRPC, TRANSPORT_HTTP_JSON}

_A2A_PREFERRED_TRANSPORT_MAP: dict[str, str] = {
    "HTTP+JSON": TRANSPORT_HTTP_JSON,
    "JSONRPC": TRANSPORT_JSONRPC,
}


def normalize_a2a_agent_path(value: Any) -> str:
    """Normalize user-provided A2A agent path input into a slash-free SEO slug."""
    if value is None:
        raise ValueError("A2A agent path is required")

    raw_path = str(value).strip().lower()
    normalized_path = re.sub(r"[^a-z0-9]+", "-", raw_path)
    normalized_path = re.sub(r"-+", "-", normalized_path).strip("-")

    if not normalized_path:
        raise ValueError("A2A agent path must contain at least one letter or number and cannot be '/'")

    return normalized_path


def preferred_transport_to_config_type(preferred_transport: str) -> str:
    """Map AgentCard preferredTransport to registry config.type."""
    return _A2A_PREFERRED_TRANSPORT_MAP.get(preferred_transport.upper(), TRANSPORT_JSONRPC)


# ========== Registry-Specific Models ==========


class AgentConfig(BaseModel):
    """Registry-specific agent configuration (user-provided metadata)."""

    title: str = Field(description="User-provided display title for the agent")
    description: str = Field(default="", description="User-provided description of the agent")
    # Service root supplied by the user for card DISCOVERY only. The registry appends
    # /.well-known/agent-card.json (and fallbacks) to this to fetch the card.
    url: HttpUrl | str | None = Field(
        default=None, description="User-provided agent endpoint URL (where agent card was fetched from)"
    )
    type: str = Field(description="Transport type: jsonrpc, grpc, http_json")
    runtimeAccess: AgentCoreRuntimeAccessConfig | None = Field(
        default=None,
        description="Per-agent runtime auth mode used for federated AgentCore data-plane calls",
    )

    model_config = ConfigDict(populate_by_name=True)


class WellKnownConfig(BaseModel):
    """Manual .well-known sync configuration - stores sync state only, URL is in config.url"""

    enabled: bool = Field(default=False, description="Whether well-known sync is enabled")

    # Sync metadata (manual refresh only) - URL comes from config.url
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
    path: str = Field(
        ...,
        description="Registry path in slug format (no slashes, e.g., 'deep-intel'). "
        "Used in proxy routes /proxy/a2a/{path}. Input paths with slashes are automatically normalized.",
    )

    # ========== A2A Protocol Card (SDK - ORIGINAL DATA) ==========
    # Fetched AgentCard document. card.url is the spec-defined invocation endpoint
    card: AgentCard = Field(description="A2A protocol-compliant agent card (validated by SDK, unmodified)")

    # ========== Registry-specific Configuration ==========
    config: AgentConfig | None = Field(
        default=None, description="User-provided agent configuration (title, description, URL, transport type)"
    )

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

    vectorContentHash: str | None = Field(
        default=None,
        description="SHA-256 of vectorized page_content; used to skip re-embedding when content is unchanged",
    )

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

    # ========== Field Derivation ==========
    @model_validator(mode="before")
    @classmethod
    def _normalize_path(cls, data: Any) -> Any:
        """
        Normalize path input to slug format (no slashes).

        Converts any path-like input (e.g., /agentcore/a2a/agent-1) to slug format (agentcore-a2a-agent-1).
        The normalized path is used in the A2A proxy routes: /proxy/a2a/{path}

        Handles edge cases:
        - Leading/trailing slashes: /path/ -> path
        - Multiple consecutive slashes: ///path/// -> path
        - Internal slashes: /a/b/c -> a-b-c
        - Consecutive internal slashes: /a///b -> a-b

        Uniqueness is enforced by the MongoDB unique index on the path field.
        If the normalized path conflicts with an existing agent, MongoDB will raise a DuplicateKeyError,
        which should be caught and handled at the service/API layer with a proper HTTP 409 response.
        """
        if isinstance(data, dict) and "path" in data:
            data = dict(data)
            data["path"] = normalize_a2a_agent_path(data["path"])
        return data

    # ========== Lifecycle Hooks ==========
    @before_event(Insert, Replace, Save)
    async def update_timestamps(self):
        """Update timestamps before saving."""
        self.updatedAt = datetime.now(UTC)
        if not self.createdAt:
            self.createdAt = datetime.now(UTC)

    @before_event(Insert, Replace, Save, SaveChanges, Update)
    def _refresh_content_hash(self):
        """Recompute vectorContentHash before every write.

        Service layer captures the hash before .save() and compares after to decide whether to
        call sync_to_vector_db (full rebuild) or update_entity_metadata (metadata-only patch).
        This contract holds as long as isEnabled/status are NOT included in page_content — if
        to_documents() ever embeds those fields, toggle paths will incorrectly trigger full syncs.
        """
        docs = self.to_documents()
        contents = sorted(doc.page_content for doc in docs)
        per_doc_hashes = [hashlib.sha256(c.encode()).hexdigest() for c in contents]
        self.vectorContentHash = hashlib.sha256("".join(per_doc_hashes).encode()).hexdigest()

    # ========== Vector Search Integration ==========
    COLLECTION_NAME: ClassVar[str] = "A2a_agents"

    def to_searchable_text(self) -> str:
        """Generate searchable text for vector embedding.

        Includes all natural-language and structured fields from the AgentCard
        so that semantic discovery covers capabilities, transport, I/O modes,
        security scheme names, and skill details — not just title/description.
        """
        title = self.config.title if self.config else self.card.name
        # Prefer the registry-provided description; fall back to the card description.
        description = (self.config.description if self.config else None) or self.card.description or ""
        # Include card name only when it differs from the display title (e.g. slug vs human name).
        card_name = self.card.name
        parts = [f"Title: {title}"]
        if card_name and card_name != title:
            parts.append(f"Name: {card_name}")
        if description:
            parts.append(f"Description: {description}")
        parts.append(f"Path: {self.path}")

        # Protocol and transport info — helps queries like "find me a streaming agent"
        if self.card.protocol_version:
            parts.append(f"Protocol Version: {self.card.protocol_version}")
        if self.card.preferred_transport:
            parts.append(f"Preferred Transport: {self.card.preferred_transport}")
        if self.card.default_input_modes:
            parts.append(f"Input Modes: {', '.join(self.card.default_input_modes)}")
        if self.card.default_output_modes:
            parts.append(f"Output Modes: {', '.join(self.card.default_output_modes)}")

        # Capabilities — searchable by feature name
        if self.card.capabilities:
            cap = self.card.capabilities
            cap_parts: list[str] = []
            if getattr(cap, "streaming", False):
                cap_parts.append("streaming")
            if getattr(cap, "push_notifications", False):
                cap_parts.append("push notifications")
            if getattr(cap, "state_transition_history", False):
                cap_parts.append("state transition history")
            if cap_parts:
                parts.append(f"Capabilities: {', '.join(cap_parts)}")

        # Security scheme names — helps queries like "find me an OAuth agent"
        if self.card.security_schemes:
            scheme_names = list(self.card.security_schemes.keys())
            if scheme_names:
                parts.append(f"Security Schemes: {', '.join(scheme_names)}")

        if self.card.skills:
            skills_text = "\n".join(
                f"Skill {i + 1}: {skill.name} - {skill.description} (Tags: {', '.join(skill.tags or [])})"
                for i, skill in enumerate(self.card.skills)
            )
            parts.append(f"Skills:\n{skills_text}")

        if self.tags:
            parts.append(f"Tags: {', '.join(self.tags)}")

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
        # Backward compatibility: if config is None, use card data
        agent_name = self.config.title if self.config else self.card.name

        base_metadata = {
            "collection": self.COLLECTION_NAME,
            "agent_id": agent_id,
            "agent_name": agent_name,  # Keep key stable for backward compatibility
            "path": self.path,
            "enabled": self.isEnabled,
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
                    "entity_type": A2AEntityType.AGENT,
                },
            )
        ]

        for skill in self.card.skills or []:
            skill_name = getattr(skill, "name", "") or ""
            skill_desc = getattr(skill, "description", "") or ""
            skill_tags = getattr(skill, "tags", None) or []
            skill_input_modes = getattr(skill, "input_modes", None) or []
            skill_output_modes = getattr(skill, "output_modes", None) or []
            skill_examples = getattr(skill, "examples", None) or []
            # Backward compatibility: if config is None, use card data
            agent_display_name = self.config.title if self.config else self.card.name
            skill_parts = [
                f"Agent: {agent_display_name}",
                f"Skill: {skill_name}",
            ]
            if skill_desc:
                skill_parts.append(f"Description: {skill_desc}")
            if skill_tags:
                skill_parts.append(f"Tags: {', '.join(skill_tags)}")
            if skill_input_modes:
                skill_parts.append(f"Input Modes: {', '.join(skill_input_modes)}")
            if skill_output_modes:
                skill_parts.append(f"Output Modes: {', '.join(skill_output_modes)}")
            if skill_examples:
                skill_parts.append(f"Examples: {' | '.join(skill_examples[:3])}")
            docs.append(
                LangChainDocument(
                    page_content="\n".join(skill_parts),
                    metadata={
                        **base_metadata,
                        "entity_type": A2AEntityType.SKILL,
                        "skill_name": skill_name,
                    },
                )
            )

        return docs

    def mutable_metadata(self) -> dict[str, Any]:
        """Return metadata fields that can change without affecting page_content.

        agent_name and path are intentionally excluded: both appear in page_content,
        so changing either always changes vectorContentHash and triggers a full rebuild.
        """
        meta: dict[str, Any] = {
            "enabled": self.isEnabled,
            "tags": self.tags or [],
        }
        fed = self.federationMetadata or {}
        for key in ("runtimeVersion", "agentVersion"):
            value = fed.get(key)
            if value is not None:
                meta[key] = str(value)
        return meta

    @classmethod
    def from_document(cls, document: LangChainDocument) -> dict[str, Any]:
        """Extract metadata from vector document for chat-interface discovery."""
        metadata = document.metadata or {}
        raw_score = metadata.get("relevance_score")
        result: dict[str, Any] = {
            "agent_id": metadata.get("agent_id"),
            "agent_name": metadata.get("agent_name"),
            "path": metadata.get("path"),
            "entity_type": metadata.get("entity_type"),
            "skill_name": metadata.get("skill_name"),
            "enabled": metadata.get("enabled"),
            "content": document.page_content,
            "relevance_score": round(float(raw_score), 3) if raw_score is not None else None,
            "description": document.page_content,
            "tags": metadata.get("tags") or [],
        }
        return result

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
                - config: AgentConfig with title, description, type (required; registry display metadata, distinct from the protocol card `name`)
                - isEnabled: Enabled state (default: False)
                - registeredBy: Username or service account
                - tags: List of tags

        Returns:
            A2AAgent instance

        Raises:
            ValueError: If validation fails or author/path/config field is missing
        """
        # Validate required registry fields
        if "author" not in registry_fields:
            raise ValueError("'author' field is required in registry_fields for ACL integration")

        if not path:
            raise ValueError("'path' is required for registry agent")

        if "config" not in registry_fields:
            raise ValueError("'config' field is required in registry_fields")

        # Remove path from card_data if it exists (SDK doesn't support it)
        card_data_clean = {k: v for k, v in card_data.items() if k != "path"}

        # SDK validates the entire card structure
        try:
            agent_card = AgentCard(**card_data_clean)
        except Exception as e:
            raise ValueError(f"Invalid A2A agent card: {str(e)}")

        # Validate config
        config = registry_fields["config"]
        if not isinstance(config, AgentConfig):
            raise ValueError("'config' must be an AgentConfig instance")

        # Create MongoDB document
        return cls(
            path=path,
            card=agent_card,
            config=config,
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
    "AgentConfig",
    "WellKnownConfig",
    "STATUS_ACTIVE",
    "STATUS_INACTIVE",
    "STATUS_ERROR",
    "VALID_STATUSES",
    "TRANSPORT_JSONRPC",
    "TRANSPORT_GRPC",
    "TRANSPORT_HTTP_JSON",
    "VALID_TRANSPORT_TYPES",
    "preferred_transport_to_config_type",
    "normalize_a2a_agent_path",
]
