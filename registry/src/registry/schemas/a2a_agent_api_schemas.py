"""
Pydantic Schemas for A2A Agent Management API v1

These schemas define the request and response models for the
A2A Agent Management endpoints based on the API documentation.

All schemas use camelCase for API input/output and for MongoDB storage.
"""

from datetime import datetime
from typing import Any

from pydantic import Field, HttpUrl

from .acl_schema import ResourcePermissions
from .case_conversion import APIBaseModel

# ==================== Nested Models ====================


class AgentSkillInput(APIBaseModel):
    """Input schema for agent skill"""

    id: str = Field(description="Unique skill identifier")
    name: str = Field(description="Human-readable skill name")
    description: str = Field(description="Detailed skill description")
    tags: list[str] = Field(default_factory=list, description="Skill categorization tags")
    examples: list[str] | None = Field(None, description="Usage examples")
    inputModes: list[str] | None = Field(None, description="Skill-specific input MIME types")
    outputModes: list[str] | None = Field(None, description="Skill-specific output MIME types")
    security: list[dict[str, list[str]]] | None = Field(None, description="Skill-level security requirements")


class AgentSkillOutput(APIBaseModel):
    """Output schema for agent skill"""

    id: str
    name: str
    description: str
    tags: list[str] = []
    inputModes: list[str] | None = None
    outputModes: list[str] | None = None


class AgentProviderInput(APIBaseModel):
    """Input schema for agent provider"""

    organization: str = Field(description="Provider organization name")
    url: str = Field(description="Provider website or documentation URL")


class AgentProviderOutput(APIBaseModel):
    """Output schema for agent provider"""

    organization: str
    url: str


class WellKnownInfo(APIBaseModel):
    """Well-known configuration info"""

    enabled: bool
    url: str | None = None
    lastSyncAt: datetime | None = None
    lastSyncStatus: str | None = None
    lastSyncVersion: str | None = None


# ==================== Request Schemas ====================


class AgentCreateRequest(APIBaseModel):
    """Request schema for creating a new agent"""

    path: str = Field(description="Registry path (e.g., /code-reviewer)")
    name: str = Field(description="Agent name")
    description: str = Field(default="", description="Agent description")
    url: HttpUrl | str = Field(description="Agent endpoint URL")
    version: str = Field(description="Agent version")
    protocolVersion: str = Field(default="1.0", description="A2A protocol version")
    capabilities: dict[str, Any] = Field(
        default_factory=dict, description="Feature declarations (e.g., {'streaming': true})"
    )
    skills: list[AgentSkillInput] = Field(default_factory=list, description="Agent capabilities (skills)")
    securitySchemes: dict[str, Any] = Field(default_factory=dict, description="Supported authentication methods")
    preferredTransport: str = Field(default="HTTP+JSON", description="Preferred transport protocol")
    defaultInputModes: list[str] = Field(
        default_factory=lambda: ["text/plain"], description="Supported input MIME types"
    )
    defaultOutputModes: list[str] = Field(
        default_factory=lambda: ["application/json"], description="Supported output MIME types"
    )
    provider: AgentProviderInput | None = Field(None, description="Agent provider information")
    tags: list[str] = Field(default_factory=list, description="Categorization tags")
    enabled: bool = Field(default=False, description="Whether agent is enabled in registry")


class AgentUpdateRequest(APIBaseModel):
    """Request schema for updating an agent (partial update)"""

    name: str | None = None
    description: str | None = None
    version: str | None = None
    skills: list[AgentSkillInput] | None = None
    tags: list[str] | None = None
    enabled: bool | None = None
    capabilities: dict[str, Any] | None = None
    securitySchemes: dict[str, Any] | None = None
    preferredTransport: str | None = None
    defaultInputModes: list[str] | None = None
    defaultOutputModes: list[str] | None = None
    provider: AgentProviderInput | None = None


class AgentToggleRequest(APIBaseModel):
    """Request schema for toggling agent status"""

    enabled: bool = Field(description="New enabled state")


# ==================== Response Schemas ====================


class PaginationMetadata(APIBaseModel):
    """Pagination metadata"""

    total: int
    page: int
    perPage: int
    totalPages: int


class AgentListItem(APIBaseModel):
    """Agent item in list response"""

    id: str
    path: str
    name: str
    description: str
    url: str
    version: str
    protocolVersion: str
    tags: list[str]
    numSkills: int
    enabled: bool
    status: str
    permissions: ResourcePermissions
    author: str
    createdAt: datetime
    updatedAt: datetime


class AgentListResponse(APIBaseModel):
    """Response schema for listing agents"""

    agents: list[AgentListItem]
    pagination: PaginationMetadata


class AgentStatsResponse(APIBaseModel):
    """Response schema for agent statistics"""

    totalAgents: int
    enabledAgents: int
    disabledAgents: int
    byStatus: dict[str, int]
    byTransport: dict[str, int]
    totalSkills: int
    averageSkillsPerAgent: float


class AgentDetailResponse(APIBaseModel):
    """
    Unified response schema for agent detail operations

    Used for:
    - GET /agents/{path} - Get agent details
    - POST /agents - Create agent
    - PUT /agents/{path} - Update agent
    - POST /agents/{path}/toggle - Toggle agent
    - GET /agents/{path}/skills - Get agent skills
    """

    id: str
    path: str
    name: str
    description: str
    url: str
    version: str
    protocolVersion: str
    capabilities: dict[str, Any]
    skills: list[AgentSkillOutput]
    securitySchemes: dict[str, Any]
    preferredTransport: str
    defaultInputModes: list[str]
    defaultOutputModes: list[str]
    provider: AgentProviderOutput | None = None
    tags: list[str]
    status: str
    enabled: bool
    permissions: ResourcePermissions
    author: str
    wellKnown: WellKnownInfo | None = None
    createdAt: datetime
    updatedAt: datetime


class AgentSkillsResponse(APIBaseModel):
    """Response schema for agent skills"""

    agentId: str
    agentName: str
    skills: list[AgentSkillOutput]
    totalSkills: int


class WellKnownSyncResponse(APIBaseModel):
    """Response schema for well-known sync"""

    message: str
    syncStatus: str
    syncedAt: datetime
    version: str
    changes: list[str]


# ==================== Converter Functions ====================


def convert_to_list_item(agent: Any, acl_permission: int | ResourcePermissions) -> AgentListItem:
    """Convert A2AAgent document to list item"""
    from registry_pkgs.models.enums import PermissionBits

    if isinstance(acl_permission, ResourcePermissions):
        permissions = acl_permission
    else:
        permissions = ResourcePermissions(
            VIEW=bool(acl_permission & PermissionBits.VIEW),
            EDIT=bool(acl_permission & PermissionBits.EDIT),
            DELETE=bool(acl_permission & PermissionBits.DELETE),
            SHARE=bool(acl_permission & PermissionBits.SHARE),
        )

    return AgentListItem(
        id=str(agent.id),
        path=agent.path,
        name=agent.card.name,
        description=agent.card.description,
        url=str(agent.card.url),
        version=agent.card.version,
        protocolVersion=agent.card.protocol_version,
        tags=agent.tags,
        numSkills=len(agent.card.skills or []),
        enabled=agent.isEnabled,
        status=agent.status,
        permissions=permissions,
        author=str(agent.author),
        createdAt=agent.createdAt,
        updatedAt=agent.updatedAt,
    )


def convert_to_detail(agent: Any, acl_permission: int | ResourcePermissions) -> AgentDetailResponse:
    """Convert A2AAgent document to detail response"""
    from registry_pkgs.models.enums import PermissionBits

    if isinstance(acl_permission, ResourcePermissions):
        permissions = acl_permission
    else:
        permissions = ResourcePermissions(
            VIEW=bool(acl_permission & PermissionBits.VIEW),
            EDIT=bool(acl_permission & PermissionBits.EDIT),
            DELETE=bool(acl_permission & PermissionBits.DELETE),
            SHARE=bool(acl_permission & PermissionBits.SHARE),
        )

    skills_output = [
        AgentSkillOutput(
            id=skill.id,
            name=skill.name,
            description=skill.description,
            tags=skill.tags or [],
            inputModes=skill.input_modes if hasattr(skill, "input_modes") else None,
            outputModes=skill.output_modes if hasattr(skill, "output_modes") else None,
        )
        for skill in (agent.card.skills or [])
    ]

    provider_output = None
    if agent.card.provider:
        provider_output = AgentProviderOutput(
            organization=agent.card.provider.organization, url=agent.card.provider.url
        )

    well_known_info = None
    if agent.wellKnown:
        well_known_info = WellKnownInfo(
            enabled=agent.wellKnown.enabled,
            url=str(agent.wellKnown.url) if agent.wellKnown.url else None,
            lastSyncAt=agent.wellKnown.lastSyncAt,
            lastSyncStatus=agent.wellKnown.lastSyncStatus,
            lastSyncVersion=agent.wellKnown.lastSyncVersion,
        )

    capabilities_dict = {}
    if agent.card.capabilities:
        if hasattr(agent.card.capabilities, "model_dump"):
            capabilities_dict = agent.card.capabilities.model_dump(exclude_none=True)
        elif isinstance(agent.card.capabilities, dict):
            capabilities_dict = agent.card.capabilities
        else:
            capabilities_dict = dict(agent.card.capabilities)

    security_schemes_dict = {}
    if agent.card.security_schemes:
        if hasattr(agent.card.security_schemes, "model_dump"):
            security_schemes_dict = agent.card.security_schemes.model_dump(exclude_none=True)
        elif isinstance(agent.card.security_schemes, dict):
            security_schemes_dict = agent.card.security_schemes
        else:
            security_schemes_dict = dict(agent.card.security_schemes)

    return AgentDetailResponse(
        id=str(agent.id),
        path=agent.path,
        name=agent.card.name,
        description=agent.card.description,
        url=str(agent.card.url),
        version=agent.card.version,
        protocolVersion=agent.card.protocol_version,
        capabilities=capabilities_dict,
        skills=skills_output,
        securitySchemes=security_schemes_dict,
        preferredTransport=agent.card.preferred_transport,
        defaultInputModes=agent.card.default_input_modes or [],
        defaultOutputModes=agent.card.default_output_modes or [],
        provider=provider_output,
        tags=agent.tags,
        status=agent.status,
        enabled=agent.isEnabled,
        permissions=permissions,
        author=str(agent.author),
        wellKnown=well_known_info,
        createdAt=agent.createdAt,
        updatedAt=agent.updatedAt,
    )


def convert_to_skills_response(agent: Any) -> AgentSkillsResponse:
    """Convert A2AAgent document to skills response"""
    skills_output = [
        AgentSkillOutput(
            id=skill.id,
            name=skill.name,
            description=skill.description,
            tags=skill.tags or [],
            inputModes=skill.input_modes if hasattr(skill, "input_modes") else None,
            outputModes=skill.output_modes if hasattr(skill, "output_modes") else None,
        )
        for skill in (agent.card.skills or [])
    ]

    return AgentSkillsResponse(
        agentId=str(agent.id),
        agentName=agent.card.name,
        skills=skills_output,
        totalSkills=len(agent.card.skills or []),
    )
