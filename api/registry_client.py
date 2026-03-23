#!/usr/bin/env python3
"""
MCP Gateway Registry Client - Standalone Pydantic-based client for the Registry API.

This client provides a type-safe interface to the MCP Gateway Registry API endpoints
documented in:
- /home/ubuntu/repos/mcp-gateway-registry/docs/api-specs/server-management.yaml (Server Management)
- /home/ubuntu/repos/mcp-gateway-registry/docs/api-specs/a2a-agent-management.yaml (Agent Management)

Authentication is handled via JWT tokens retrieved from AWS SSM Parameter Store using
the get-m2m-token.sh script.
"""

import json
import logging
from datetime import datetime
from enum import StrEnum
from typing import Any
from urllib.parse import quote

import requests
from pydantic import BaseModel, ConfigDict, Field

from registry.core.config import settings

# Configure logging
logging.basicConfig(level=settings.log_level, format=settings.log_format)

logger = logging.getLogger(__name__)


class HealthStatus(StrEnum):
    """Health status enumeration for servers."""

    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"
    DISABLED = "disabled"


class ServiceRegistration(BaseModel):
    """Service registration request model (UI-based registration)."""

    name: str = Field(..., description="Service name")
    description: str = Field(..., description="Service description")
    path: str = Field(..., description="Service path")
    proxy_pass_url: str = Field(..., description="Proxy pass URL")
    tags: str | None = Field(None, description="Comma-separated tags")
    num_tools: int | None = Field(None, description="Number of tools")
    num_stars: int | None = Field(None, description="Number of stars")
    is_python: bool | None = Field(None, description="Is Python server")
    license: str | None = Field(None, description="License type")


class InternalServiceRegistration(BaseModel):
    """Internal service registration model (Admin/M2M registration)."""

    service_path: str = Field(..., alias="path", description="Service path (e.g., /cloudflare-docs)")
    name: str | None = Field(None, description="Service name")
    description: str | None = Field(None, description="Service description")
    proxy_pass_url: str | None = Field(None, description="Proxy pass URL")
    auth_provider: str | None = Field(None, description="Authentication provider")
    auth_type: str | None = Field(None, description="Authentication type")
    supported_transports: list[str] | None = Field(None, description="Supported transports")
    headers: dict[str, str] | None = Field(None, description="Custom headers")
    tool_list_json: str | None = Field(None, description="Tool list as JSON string")
    overwrite: bool | None = Field(False, description="Overwrite if exists")

    model_config = ConfigDict(populate_by_name=True)


class Server(BaseModel):
    """Server information model."""

    path: str = Field(..., description="Service path")
    display_name: str = Field(..., description="Service display name")
    description: str = Field(..., description="Service description")
    is_enabled: bool = Field(..., description="Whether service is enabled")
    health_status: HealthStatus = Field(..., description="Health status")


class ServerDetail(BaseModel):
    """Detailed server information model."""

    path: str = Field(..., description="Service path")
    name: str = Field(..., description="Service name")
    description: str = Field(..., description="Service description")
    url: str = Field(..., description="Service URL")
    is_enabled: bool = Field(..., description="Whether service is enabled")
    num_tools: int = Field(..., description="Number of tools")
    health_status: str = Field(..., description="Health status")
    last_health_check: datetime | None = Field(None, description="Last health check timestamp")


class ServerListResponse(BaseModel):
    """Server list response model."""

    servers: list[Server] = Field(..., description="List of servers")


class ServiceResponse(BaseModel):
    """Service operation response model."""

    path: str = Field(..., description="Service path")
    name: str = Field(..., description="Service name")
    message: str = Field(..., description="Response message")


class ToggleResponse(BaseModel):
    """Toggle service response model."""

    path: str = Field(..., description="Service path")
    is_enabled: bool = Field(..., description="Current enabled status")
    message: str = Field(..., description="Response message")


class ErrorResponse(BaseModel):
    """Error response model."""

    detail: str = Field(..., description="Error detail message")
    error_code: str | None = Field(None, description="Error code")
    request_id: str | None = Field(None, description="Request ID")


class SecurityScanResult(BaseModel):
    """Security scan result model."""

    analysis_results: dict[str, Any] = Field(..., description="Analysis results by analyzer")
    tool_results: list[dict[str, Any]] = Field(..., description="Detailed tool scan results")


class RescanResponse(BaseModel):
    """Server rescan response model."""

    server_url: str = Field(..., description="Server URL that was scanned")
    server_path: str = Field(..., description="Server path")
    scan_timestamp: str = Field(..., description="Scan timestamp")
    is_safe: bool = Field(..., description="Whether server is safe")
    critical_issues: int = Field(..., description="Number of critical issues")
    high_severity: int = Field(..., description="Number of high severity issues")
    medium_severity: int = Field(..., description="Number of medium severity issues")
    low_severity: int = Field(..., description="Number of low severity issues")
    analyzers_used: list[str] = Field(..., description="Analyzers used in scan")
    scan_failed: bool = Field(..., description="Whether scan failed")
    error_message: str | None = Field(None, description="Error message if scan failed")
    raw_output: dict[str, Any] | None = Field(None, description="Raw scan output")


class AgentSecurityScanResponse(BaseModel):
    """Agent security scan results response model."""

    analysis_results: dict[str, Any] = Field(default_factory=dict, description="Analysis results by analyzer")
    scan_results: dict[str, Any] = Field(default_factory=dict, description="Scan results and metadata")


class AgentRescanResponse(BaseModel):
    """Agent rescan response model."""

    agent_path: str = Field(..., description="Agent path")
    agent_url: str = Field(..., description="Agent URL that was scanned")
    scan_timestamp: str = Field(..., description="Scan timestamp")
    is_safe: bool = Field(..., description="Whether agent is safe")
    critical_issues: int = Field(..., description="Number of critical issues")
    high_severity: int = Field(..., description="Number of high severity issues")
    medium_severity: int = Field(..., description="Number of medium severity issues")
    low_severity: int = Field(..., description="Number of low severity issues")
    analyzers_used: list[str] = Field(..., description="Analyzers used in scan")
    scan_failed: bool = Field(..., description="Whether scan failed")
    error_message: str | None = Field(None, description="Error message if scan failed")
    output_file: str | None = Field(None, description="Path to scan output file")


class GroupListResponse(BaseModel):
    """Group list response model."""

    groups: list[dict[str, Any]] = Field(..., description="List of groups")


# Agent Management Models


class AgentProvider(StrEnum):
    """Agent provider enumeration."""

    ANTHROPIC = "anthropic"
    CUSTOM = "custom"
    OTHER = "other"


class AgentVisibility(StrEnum):
    """Agent visibility enumeration."""

    PUBLIC = "public"
    PRIVATE = "private"
    GROUP_RESTRICTED = "group-restricted"


class Provider(BaseModel):
    """
    A2A Agent Provider information.

    Represents the service provider of an agent with organization name and website URL.
    Per A2A specification, if provider is present, both organization and url are required.
    """

    organization: str = Field(..., description="Provider organization name")
    url: str = Field(..., description="Provider website or documentation URL")


class SecuritySchemeType(StrEnum):
    """Security scheme type enumeration (A2A spec values)."""

    API_KEY = "apiKey"
    HTTP = "http"
    OAUTH2 = "oauth2"
    OPENID_CONNECT = "openIdConnect"


class SecurityScheme(BaseModel):
    """
    Security scheme model.
    Note: Uses snake_case internally but serializes to camelCase for A2A compliance.
    """

    type: SecuritySchemeType = Field(..., description="Security scheme type")
    scheme: str | None = Field(
        None,
        description="HTTP auth scheme: basic, bearer, digest",
    )
    in_: str | None = Field(
        None,
        alias="in",
        description="API key location: header, query, cookie",
    )
    name: str | None = Field(
        None,
        description="Name of header/query/cookie for API key",
    )
    bearer_format: str | None = Field(
        None,
        alias="bearerFormat",
        description="Bearer token format hint (e.g., JWT)",
    )
    flows: dict[str, Any] | None = Field(
        None,
        description="OAuth2 flows configuration",
    )
    openid_connect_url: str | None = Field(
        None,
        alias="openIdConnectUrl",
        description="OpenID Connect discovery URL",
    )
    description: str | None = Field(None, description="Security scheme description")

    class Config:
        populate_by_name = True  # Allow both snake_case and camelCase on input


class Skill(BaseModel):
    """
    Agent skill definition per A2A protocol specification.
    Note: Uses snake_case internally but serializes to camelCase for A2A compliance.
    """

    id: str = Field(..., description="Unique skill identifier")
    name: str = Field(..., description="Human-readable skill name")
    description: str = Field(..., description="Detailed skill description")
    tags: list[str] = Field(default_factory=list, description="Skill categorization tags")
    examples: list[str] | None = Field(None, description="Usage scenarios and examples")
    input_modes: list[str] | None = Field(None, alias="inputModes", description="Skill-specific input MIME types")
    output_modes: list[str] | None = Field(None, alias="outputModes", description="Skill-specific output MIME types")
    security: list[dict[str, list[str]]] | None = Field(None, description="Skill-level security requirements")

    class Config:
        populate_by_name = True  # Allow both snake_case and camelCase on input


class AgentRegistration(BaseModel):
    """
    Agent registration request model matching server AgentCard schema.
    This model represents a complete agent card following the A2A protocol
    specification (v0.3.0), with extensions for MCP Gateway Registry integration.
    Note: Uses snake_case internally but serializes to camelCase for A2A compliance.
    """

    # Required A2A fields
    protocol_version: str = Field("1.0", alias="protocolVersion", description="A2A protocol version (e.g., '1.0')")
    name: str = Field(..., description="Agent name")
    description: str = Field(..., description="Agent description")
    url: str = Field(..., description="Agent endpoint URL (HTTP or HTTPS)")
    version: str = Field(..., description="Agent version")
    capabilities: dict[str, Any] = Field(
        default_factory=dict, description="Feature declarations (e.g., {'streaming': true})"
    )
    default_input_modes: list[str] = Field(
        default_factory=lambda: ["text/plain"], alias="defaultInputModes", description="Supported input MIME types"
    )
    default_output_modes: list[str] = Field(
        default_factory=lambda: ["text/plain"], alias="defaultOutputModes", description="Supported output MIME types"
    )
    skills: list[Skill] = Field(default_factory=list, description="Agent capabilities (skills)")

    # Optional A2A fields
    preferred_transport: str | None = Field(
        "JSONRPC", alias="preferredTransport", description="Preferred transport protocol: JSONRPC, GRPC, HTTP+JSON"
    )
    provider: Provider | None = Field(None, description="Agent provider information per A2A spec")
    icon_url: str | None = Field(None, alias="iconUrl", description="Agent icon URL")
    documentation_url: str | None = Field(None, alias="documentationUrl", description="Documentation URL")
    security_schemes: dict[str, SecurityScheme] = Field(
        default_factory=dict, alias="securitySchemes", description="Supported authentication methods"
    )
    security: list[dict[str, list[str]]] | None = Field(None, description="Security requirements array")
    supports_authenticated_extended_card: bool | None = Field(
        None, alias="supportsAuthenticatedExtendedCard", description="Supports extended card with auth"
    )
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")

    # MCP Gateway Registry extensions (optional - not part of A2A spec)
    path: str | None = Field(
        None, description="Registry path (e.g., /agents/my-agent). Optional - auto-generated if not provided."
    )
    tags: list[str] = Field(default_factory=list, description="Categorization tags")
    is_enabled: bool = Field(False, alias="isEnabled", description="Whether agent is enabled in registry")
    num_stars: int = Field(0, ge=0, alias="numStars", description="Community rating")
    license: str = Field("N/A", description="License information")
    registered_at: datetime | None = Field(None, alias="registeredAt", description="Registration timestamp")
    updated_at: datetime | None = Field(None, alias="updatedAt", description="Last update timestamp")
    registered_by: str | None = Field(None, alias="registeredBy", description="Username who registered agent")
    visibility: str = Field("public", description="public, private, or group-restricted")
    allowed_groups: list[str] = Field(default_factory=list, alias="allowedGroups", description="Groups with access")
    signature: str | None = Field(None, description="JWS signature for card integrity")
    trust_level: str = Field("unverified", alias="trustLevel", description="unverified, community, verified, trusted")

    class Config:
        populate_by_name = True  # Allow both snake_case and camelCase on input


class AgentCard(BaseModel):
    """Agent card model (summary view)."""

    name: str = Field(..., description="Agent name")
    path: str = Field(..., description="Agent path")
    url: str = Field(..., description="Agent URL")
    num_skills: int = Field(..., description="Number of skills")
    registered_at: datetime = Field(..., description="Registration timestamp")
    is_enabled: bool = Field(..., description="Whether agent is enabled")


class AgentRegistrationResponse(BaseModel):
    """Agent registration response model."""

    message: str = Field(..., description="Response message")
    agent: AgentCard = Field(..., description="Registered agent card")


class SkillDetail(BaseModel):
    """
    Detailed skill model - same as Skill.
    Note: Uses snake_case internally but serializes to camelCase for A2A compliance.
    """

    id: str = Field(..., description="Unique skill identifier")
    name: str = Field(..., description="Human-readable skill name")
    description: str = Field(..., description="Detailed skill description")
    tags: list[str] = Field(default_factory=list, description="Skill categorization tags")
    examples: list[str] | None = Field(None, description="Usage scenarios and examples")
    input_modes: list[str] | None = Field(None, alias="inputModes", description="Skill-specific input MIME types")
    output_modes: list[str] | None = Field(None, alias="outputModes", description="Skill-specific output MIME types")
    security: list[dict[str, list[str]]] | None = Field(None, description="Skill-level security requirements")

    class Config:
        populate_by_name = True  # Allow both snake_case and camelCase on input


class AgentDetail(BaseModel):
    """
    Detailed agent model matching server AgentCard schema.
    This model represents a complete agent card following the A2A protocol
    specification (v0.3.0), with extensions for MCP Gateway Registry integration.
    Note: Uses snake_case internally but serializes to camelCase for A2A compliance.
    """

    # Required A2A fields
    protocol_version: str = Field(..., alias="protocolVersion", description="A2A protocol version")
    name: str = Field(..., description="Agent name")
    description: str = Field(..., description="Agent description")
    url: str = Field(..., description="Agent endpoint URL")
    version: str = Field(..., description="Agent version")
    capabilities: dict[str, Any] = Field(
        default_factory=dict, description="Feature declarations (e.g., {'streaming': true})"
    )
    default_input_modes: list[str] = Field(
        default_factory=lambda: ["text/plain"], alias="defaultInputModes", description="Supported input MIME types"
    )
    default_output_modes: list[str] = Field(
        default_factory=lambda: ["text/plain"], alias="defaultOutputModes", description="Supported output MIME types"
    )
    skills: list[SkillDetail] = Field(default_factory=list, description="Agent capabilities (skills)")

    # Optional A2A fields
    preferred_transport: str | None = Field(
        "JSONRPC", alias="preferredTransport", description="Preferred transport protocol: JSONRPC, GRPC, HTTP+JSON"
    )
    provider: Provider | None = Field(None, description="Agent provider information per A2A spec")
    icon_url: str | None = Field(None, alias="iconUrl", description="Agent icon URL")
    documentation_url: str | None = Field(None, alias="documentationUrl", description="Documentation URL")
    security_schemes: dict[str, SecurityScheme] = Field(
        default_factory=dict, alias="securitySchemes", description="Supported authentication methods"
    )
    security: list[dict[str, list[str]]] | None = Field(None, description="Security requirements array")
    supports_authenticated_extended_card: bool | None = Field(
        None, alias="supportsAuthenticatedExtendedCard", description="Supports extended card with auth"
    )
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")

    # MCP Gateway Registry extensions (optional - not part of A2A spec)
    path: str | None = Field(None, description="Registry path")
    tags: list[str] = Field(default_factory=list, description="Categorization tags")
    is_enabled: bool = Field(False, alias="isEnabled", description="Whether agent is enabled")
    num_stars: int = Field(0, ge=0, alias="numStars", description="Community rating")
    license: str = Field("N/A", description="License information")
    registered_at: datetime | None = Field(None, alias="registeredAt", description="Registration timestamp")
    updated_at: datetime | None = Field(None, alias="updatedAt", description="Last update timestamp")
    registered_by: str | None = Field(None, alias="registeredBy", description="Username who registered agent")
    visibility: str = Field("public", description="Visibility level")
    allowed_groups: list[str] = Field(default_factory=list, alias="allowedGroups", description="Groups with access")
    trust_level: str = Field("unverified", alias="trustLevel", description="Trust level")
    signature: str | None = Field(None, description="JWS signature for card integrity")

    class Config:
        populate_by_name = True  # Allow both snake_case and camelCase on input


class AgentListItem(BaseModel):
    """
    Agent list item model (AgentInfo from server).
    Note: Uses snake_case internally but serializes to camelCase for A2A compliance.
    """

    id: str = Field(..., description="Agent ID")
    path: str = Field(..., description="Agent path")
    name: str = Field(..., description="Agent name")
    description: str = Field(default="", description="Agent description")
    url: str = Field(..., description="Agent URL")
    version: str = Field(..., description="Agent version")
    protocol_version: str = Field(..., alias="protocolVersion", description="Protocol version")
    tags: list[str] = Field(default_factory=list, description="Categorization tags")
    num_skills: int = Field(default=0, alias="numSkills", description="Number of skills")
    skills: list[Skill] = Field(default_factory=list, description="Agent skills with full details")
    enabled: bool = Field(default=False, description="Whether agent is enabled")
    status: str = Field(..., description="Agent status (active, inactive, error)")
    permissions: dict[str, bool] = Field(default_factory=dict, description="User permissions for this agent")
    author: str = Field(..., description="Agent author ID")
    created_at: str = Field(..., alias="createdAt", description="Creation timestamp")
    updated_at: str = Field(..., alias="updatedAt", description="Last update timestamp")

    class Config:
        populate_by_name = True  # Allow both snake_case and camelCase on input


class AgentListResponse(BaseModel):
    """Agent list response model."""

    agents: list[AgentListItem] = Field(..., description="List of agents")
    total_count: int = Field(..., description="Total count of agents")


class AgentToggleResponse(BaseModel):
    """Agent toggle response model."""

    path: str = Field(..., description="Agent path")
    is_enabled: bool = Field(..., description="Current enabled status")
    message: str = Field(..., description="Response message")


class SkillDiscoveryRequest(BaseModel):
    """Skill-based discovery request model."""

    skills: list[str] = Field(..., description="List of required skills")
    tags: list[str] | None = Field(None, description="Optional tag filters")


class DiscoveredAgent(BaseModel):
    """Discovered agent model (skill-based)."""

    path: str = Field(..., description="Agent path")
    name: str = Field(..., description="Agent name")
    relevance_score: float = Field(..., description="Matching score (0.0 to 1.0)")
    matching_skills: list[str] = Field(..., description="Matching skills")


class AgentDiscoveryResponse(BaseModel):
    """Agent discovery response model (skill-based)."""

    agents: list[DiscoveredAgent] = Field(..., description="Discovered agents")


class SemanticDiscoveredAgent(BaseModel):
    """Semantically discovered agent model."""

    path: str = Field(..., description="Agent path")
    name: str = Field(..., description="Agent name")
    relevance_score: float = Field(..., description="Semantic similarity score (0.0 to 1.0)")
    description: str = Field(..., description="Agent description")


class AgentSemanticDiscoveryResponse(BaseModel):
    """Agent semantic discovery response model."""

    agents: list[SemanticDiscoveredAgent] = Field(..., description="Semantically discovered agents")


class RatingDetail(BaseModel):
    """Individual rating detail."""

    user: str = Field(..., description="Username who submitted the rating")
    rating: int = Field(..., ge=1, le=5, description="Rating value (1-5 stars)")


class RatingRequest(BaseModel):
    """Rating submission request."""

    rating: int = Field(..., ge=1, le=5, description="Rating value (1-5 stars)")


class RatingResponse(BaseModel):
    """Rating submission response."""

    message: str = Field(..., description="Success message")
    average_rating: float = Field(..., ge=1.0, le=5.0, description="Updated average rating")


class RatingInfoResponse(BaseModel):
    """Rating information response."""

    num_stars: float = Field(..., ge=0.0, le=5.0, description="Average rating (0.0 if no ratings)")
    rating_details: list[RatingDetail] = Field(..., description="Individual ratings (max 100)")


# Anthropic Registry API Models (v0.1)


class AnthropicRepository(BaseModel):
    """Repository metadata for MCP server source code (Anthropic Registry API)."""

    url: str = Field(..., description="Repository URL for browsing source code")
    source: str = Field(..., description="Repository hosting service identifier (e.g., 'github')")
    id: str | None = Field(None, description="Repository ID from hosting service")
    subfolder: str | None = Field(None, description="Path within monorepo")


class AnthropicStdioTransport(BaseModel):
    """Standard I/O transport configuration (Anthropic Registry API)."""

    type: str = Field(default="stdio")
    command: str | None = Field(None, description="Command to execute")
    args: list[str] | None = Field(None, description="Command arguments")
    env: dict[str, str] | None = Field(None, description="Environment variables")


class AnthropicStreamableHttpTransport(BaseModel):
    """HTTP-based transport configuration (Anthropic Registry API)."""

    type: str = Field(default="streamable-http")
    url: str = Field(..., description="HTTP endpoint URL")
    headers: dict[str, str] | None = Field(None, description="HTTP headers")


class AnthropicSseTransport(BaseModel):
    """Server-Sent Events transport configuration (Anthropic Registry API)."""

    type: str = Field(default="sse")
    url: str = Field(..., description="SSE endpoint URL")


class AnthropicPackage(BaseModel):
    """Package information for MCP server distribution (Anthropic Registry API)."""

    registryType: str = Field(..., description="Registry type (npm, pypi, oci, etc.)")
    identifier: str = Field(..., description="Package identifier or URL")
    version: str = Field(..., description="Specific package version")
    registryBaseUrl: str | None = Field(None, description="Base URL of package registry")
    transport: dict[str, Any] = Field(..., description="Transport configuration")
    runtimeHint: str | None = Field(None, description="Runtime hint (npx, uvx, docker, etc.)")


class AnthropicServerDetail(BaseModel):
    """Detailed MCP server information (Anthropic Registry API)."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(..., description="Server name in reverse-DNS format")
    description: str = Field(..., description="Server description")
    version: str = Field(..., description="Server version")
    title: str | None = Field(None, description="Human-readable server name")
    repository: AnthropicRepository | None = Field(None, description="Repository information")
    websiteUrl: str | None = Field(None, description="Server website URL")
    packages: list[AnthropicPackage] | None = Field(None, description="Package distributions")
    meta: dict[str, Any] | None = Field(
        None, alias="_meta", serialization_alias="_meta", description="Extensible metadata"
    )


class AnthropicServerResponse(BaseModel):
    """Response for single server query (Anthropic Registry API)."""

    model_config = ConfigDict(populate_by_name=True)

    server: AnthropicServerDetail = Field(..., description="Server details")
    meta: dict[str, Any] | None = Field(
        None, alias="_meta", serialization_alias="_meta", description="Registry-managed metadata"
    )


class AnthropicPaginationMetadata(BaseModel):
    """Pagination information for server lists (Anthropic Registry API)."""

    nextCursor: str | None = Field(None, description="Cursor for next page")
    count: int | None = Field(None, description="Number of items in current page")


class AnthropicServerList(BaseModel):
    """Response for server list queries (Anthropic Registry API)."""

    servers: list[AnthropicServerResponse] = Field(..., description="List of servers")
    metadata: AnthropicPaginationMetadata | None = Field(None, description="Pagination info")


class AnthropicErrorResponse(BaseModel):
    """Standard error response (Anthropic Registry API)."""

    error: str = Field(..., description="Error message")


# Management API Models (IAM/User Management)


class M2MAccountRequest(BaseModel):
    """Request model for creating M2M service account."""

    name: str = Field(..., min_length=1, description="Service account name/client ID")
    groups: list[str] = Field(..., min_length=1, description="List of group names")
    description: str | None = Field(None, description="Account description")


class HumanUserRequest(BaseModel):
    """Request model for creating human user account."""

    username: str = Field(..., min_length=1, description="Username")
    email: str = Field(..., description="Email address")
    first_name: str = Field(..., min_length=1, description="First name")
    last_name: str = Field(..., min_length=1, description="Last name")
    groups: list[str] = Field(..., min_length=1, description="List of group names")
    password: str | None = Field(None, description="Initial password")


class KeycloakUserSummary(BaseModel):
    """Keycloak user summary model."""

    id: str = Field(..., description="User ID")
    username: str = Field(..., description="Username")
    email: str | None = Field(None, description="Email address")
    firstName: str | None = Field(None, description="First name")
    lastName: str | None = Field(None, description="Last name")
    enabled: bool = Field(True, description="Whether user is enabled")
    groups: list[str] = Field(default_factory=list, description="User groups")


class UserListResponse(BaseModel):
    """Response model for list users endpoint."""

    users: list[KeycloakUserSummary] = Field(default_factory=list, description="List of users")
    total: int = Field(..., description="Total number of users")


class UserDeleteResponse(BaseModel):
    """Response model for delete user endpoint."""

    username: str = Field(..., description="Deleted username")
    deleted: bool = Field(True, description="Deletion status")


class M2MAccountResponse(BaseModel):
    """Response model for M2M account creation."""

    client_id: str = Field(..., description="Client ID")
    client_secret: str = Field(..., description="Client secret")
    groups: list[str] = Field(default_factory=list, description="Assigned groups")


class GroupCreateRequest(BaseModel):
    """Request model for creating a Keycloak group."""

    name: str = Field(..., min_length=1, description="Group name")
    description: str | None = Field(None, description="Group description")


class KeycloakGroupSummary(BaseModel):
    """Keycloak group summary model."""

    id: str = Field(..., description="Group ID")
    name: str = Field(..., description="Group name")
    path: str = Field(..., description="Group path")
    attributes: dict[str, Any] | None = Field(None, description="Group attributes")


class KeycloakGroupListResponse(BaseModel):
    """Response model for list groups endpoint."""

    groups: list[KeycloakGroupSummary] = Field(default_factory=list, description="List of groups")
    total: int = Field(..., description="Total number of groups")


class GroupDeleteResponse(BaseModel):
    """Response model for delete group endpoint."""

    name: str = Field(..., description="Deleted group name")
    deleted: bool = Field(True, description="Deletion status")


class RegistryClient:
    """
    MCP Gateway Registry API client.

    Provides methods for interacting with the Registry API endpoints including:
    - Server Management: registration, removal, toggling, health checks
    - Group Management: create, delete, list groups
    - Agent Management: register, update, delete, discover agents (A2A)
    - Management API: IAM/user management, M2M accounts, user CRUD operations

    Authentication is handled via JWT tokens passed to the constructor.
    """

    def __init__(self, registry_url: str, token: str):
        """
        Initialize the Registry Client.

        Args:
            registry_url: Base URL of the registry (e.g., https://registry.mycorp.click)
            token: JWT access token for authentication
        """
        self.registry_url = registry_url.rstrip("/")
        self._token = token

        # Redact token in logs - show only first 8 characters
        redacted_token = f"{token[:8]}..." if len(token) > 8 else "***"
        logger.info(f"Initialized RegistryClient for {self.registry_url} (token: {redacted_token})")

    def _get_headers(self) -> dict[str, str]:
        """
        Get request headers with JWT token.

        Returns:
            Dictionary of HTTP headers
        """
        return {"Authorization": f"Bearer {self._token}"}

    def _make_request(
        self, method: str, endpoint: str, data: dict[str, Any] | None = None, params: dict[str, Any] | None = None
    ) -> requests.Response:
        """
        Make HTTP request to the Registry API.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint path
            data: Request body data (sent as form-encoded for POST)
            params: Query parameters

        Returns:
            Response object

        Raises:
            requests.HTTPError: If request fails
        """
        url = f"{self.registry_url}{endpoint}"
        headers = self._get_headers()

        logger.debug(f"{method} {url}")

        # Determine content type based on endpoint
        # Agent and Management API endpoints use JSON, server registration uses form data
        if endpoint.startswith("/api/agents") or endpoint.startswith("/api/management"):
            # Send as JSON for agent and management endpoints
            response = requests.request(method=method, url=url, headers=headers, json=data, params=params, timeout=30)
        else:
            # Send as form-encoded for server registration
            response = requests.request(method=method, url=url, headers=headers, data=data, params=params, timeout=30)

        try:
            response.raise_for_status()
        except requests.HTTPError:
            # For 422 errors, try to extract validation details
            if response.status_code == 422:
                try:
                    error_detail = response.json()
                    logger.error(f"Validation error details: {json.dumps(error_detail, indent=2)}")
                except Exception:
                    pass
            raise
        return response

    def register_service(self, registration: InternalServiceRegistration) -> ServiceResponse:
        """
        Register a new service in the registry.

        Args:
            registration: Service registration data

        Returns:
            Service response with registration details

        Raises:
            requests.HTTPError: If registration fails
        """
        logger.info(f"Registering service: {registration.service_path}")

        response = self._make_request(
            method="POST",
            endpoint="/api/servers/register",
            data=registration.model_dump(exclude_none=True, by_alias=True),
        )

        logger.info(f"Service registered successfully: {registration.service_path}")
        return ServiceResponse(**response.json())

    def remove_service(self, service_path: str) -> dict[str, Any]:
        """
        Remove a service from the registry.

        Args:
            service_path: Path of service to remove

        Returns:
            Response data

        Raises:
            requests.HTTPError: If removal fails
        """
        logger.info(f"Removing service: {service_path}")

        response = self._make_request(method="POST", endpoint="/api/servers/remove", data={"path": service_path})

        logger.info(f"Service removed successfully: {service_path}")
        return response.json()

    def toggle_service(self, service_path: str) -> ToggleResponse:
        """
        Toggle service enabled/disabled status.

        Args:
            service_path: Path of service to toggle

        Returns:
            Toggle response with current status

        Raises:
            requests.HTTPError: If toggle fails
        """
        logger.info(f"Toggling service: {service_path}")

        response = self._make_request(
            method="POST", endpoint="/api/servers/toggle", data={"service_path": service_path}
        )

        result = ToggleResponse(**response.json())
        logger.info(f"Service toggled: {service_path} -> enabled={result.is_enabled}")
        return result

    def list_services(self) -> ServerListResponse:
        """
        List all services in the registry.

        Returns:
            Server list response

        Raises:
            requests.HTTPError: If list operation fails
        """
        logger.info("Listing all services")

        response = self._make_request(method="GET", endpoint="/api/servers")

        response_data = response.json()
        logger.debug(f"Raw API response: {json.dumps(response_data, indent=2, default=str)}")

        try:
            result = ServerListResponse(**response_data)
            logger.info(f"Retrieved {len(result.servers)} services")
            return result
        except Exception as e:
            logger.error(f"Failed to parse server list response: {e}")
            logger.error(f"Raw response data: {json.dumps(response_data, indent=2, default=str)}")
            raise

    def healthcheck(self) -> dict[str, Any]:
        """
        Perform health check on all services.

        Returns:
            Health check response with service statuses

        Raises:
            requests.HTTPError: If health check fails
        """
        logger.info("Performing health check on all services")

        response = self._make_request(method="GET", endpoint="/api/servers/health")

        result = response.json()
        logger.info(f"Health check completed: {result.get('status', 'unknown')}")
        return result

    def add_server_to_groups(self, server_name: str, group_names: list[str]) -> dict[str, Any]:
        """
        Add a server to user groups.

        Args:
            server_name: Name of server
            group_names: List of group names

        Returns:
            Response data

        Raises:
            requests.HTTPError: If operation fails
        """
        logger.info(f"Adding server {server_name} to groups: {group_names}")

        response = self._make_request(
            method="POST",
            endpoint="/api/servers/groups/add",
            data={"server_name": server_name, "group_names": ",".join(group_names)},
        )

        logger.info("Server added to groups successfully")
        return response.json()

    def remove_server_from_groups(self, server_name: str, group_names: list[str]) -> dict[str, Any]:
        """
        Remove a server from user groups.

        Args:
            server_name: Name of server
            group_names: List of group names

        Returns:
            Response data

        Raises:
            requests.HTTPError: If operation fails
        """
        logger.info(f"Removing server {server_name} from groups: {group_names}")

        response = self._make_request(
            method="POST",
            endpoint="/api/servers/groups/remove",
            data={"server_name": server_name, "group_names": ",".join(group_names)},
        )

        logger.info("Server removed from groups successfully")
        return response.json()

    def create_group(
        self, group_name: str, description: str | None = None, create_in_keycloak: bool = False
    ) -> dict[str, Any]:
        """
        Create a new user group.

        Args:
            group_name: Name of group
            description: Group description
            create_in_keycloak: Whether to create in Keycloak

        Returns:
            Response data

        Raises:
            requests.HTTPError: If creation fails
        """
        logger.info(f"Creating group: {group_name}")

        data = {"group_name": group_name}
        if description:
            data["description"] = description
        if create_in_keycloak:
            data["create_in_keycloak"] = True

        response = self._make_request(method="POST", endpoint="/api/servers/groups/create", data=data)

        logger.info(f"Group created successfully: {group_name}")
        return response.json()

    def delete_group(self, group_name: str, delete_from_keycloak: bool = False, force: bool = False) -> dict[str, Any]:
        """
        Delete a user group.

        Args:
            group_name: Name of group
            delete_from_keycloak: Whether to delete from Keycloak
            force: Force deletion of system groups

        Returns:
            Response data

        Raises:
            requests.HTTPError: If deletion fails
        """
        logger.info(f"Deleting group: {group_name}")

        data = {"group_name": group_name}
        if delete_from_keycloak:
            data["delete_from_keycloak"] = True
        if force:
            data["force"] = True

        response = self._make_request(method="POST", endpoint="/api/servers/groups/delete", data=data)

        logger.info(f"Group deleted successfully: {group_name}")
        return response.json()

    def list_groups(self, include_keycloak: bool = True, include_scopes: bool = True) -> GroupListResponse:
        """
        List all user groups.

        Args:
            include_keycloak: Include Keycloak information
            include_scopes: Include scope information

        Returns:
            Group list response

        Raises:
            requests.HTTPError: If list operation fails
        """
        logger.info("Listing all groups")

        params = {"include_keycloak": str(include_keycloak).lower(), "include_scopes": str(include_scopes).lower()}

        response = self._make_request(method="GET", endpoint="/api/servers/groups", params=params)

        result = GroupListResponse(**response.json())
        logger.info(f"Retrieved {len(result.groups)} groups")
        return result

    # Agent Management Methods

    def register_agent(self, agent: AgentRegistration) -> AgentRegistrationResponse:
        """
        Register a new A2A agent.

        Args:
            agent: Agent registration data

        Returns:
            Agent registration response

        Raises:
            requests.HTTPError: If registration fails (409 for conflict, 422 for validation error, 403 for permission denied)
        """
        logger.info(f"Registering agent: {agent.path}")

        agent_data = agent.model_dump(exclude_none=True)
        logger.debug(f"Agent data being sent: {json.dumps(agent_data, indent=2, default=str)}")

        response = self._make_request(method="POST", endpoint="/api/agents/register", data=agent_data)

        result = AgentRegistrationResponse(**response.json())
        logger.info(f"Agent registered successfully: {agent.path}")
        return result

    def list_agents(
        self, query: str | None = None, enabled_only: bool = False, visibility: str | None = None
    ) -> AgentListResponse:
        """
        List all agents with optional filtering.

        Args:
            query: Search query string
            enabled_only: Show only enabled agents
            visibility: Filter by visibility level (public, private, internal)

        Returns:
            Agent list response

        Raises:
            requests.HTTPError: If list operation fails
        """
        logger.info("Listing agents")

        params = {}
        if query:
            params["query"] = query
        if enabled_only:
            params["enabled_only"] = "true"
        if visibility:
            params["visibility"] = visibility

        response = self._make_request(method="GET", endpoint="/api/agents", params=params)

        result = AgentListResponse(**response.json())
        logger.info(f"Retrieved {len(result.agents)} agents")
        return result

    def get_agent(self, path: str) -> AgentDetail:
        """
        Get detailed information about a specific agent.

        Args:
            path: Agent path (e.g., /code-reviewer)

        Returns:
            Agent detail

        Raises:
            requests.HTTPError: If agent not found (404) or unauthorized (403)
        """
        logger.info(f"Getting agent details: {path}")

        response = self._make_request(method="GET", endpoint=f"/api/agents{path}")

        result = AgentDetail(**response.json())
        logger.info(f"Retrieved agent details: {path}")
        return result

    def update_agent(self, path: str, agent: AgentRegistration) -> AgentDetail:
        """
        Update an existing agent.

        Args:
            path: Agent path
            agent: Updated agent data

        Returns:
            Updated agent detail

        Raises:
            requests.HTTPError: If update fails (404 for not found, 403 for permission denied, 422 for validation error)
        """
        logger.info(f"Updating agent: {path}")

        response = self._make_request(
            method="PUT", endpoint=f"/api/agents{path}", data=agent.model_dump(exclude_none=True)
        )

        result = AgentDetail(**response.json())
        logger.info(f"Agent updated successfully: {path}")
        return result

    def delete_agent(self, path: str) -> None:
        """
        Delete an agent from the registry.

        Args:
            path: Agent path

        Raises:
            requests.HTTPError: If deletion fails (404 for not found, 403 for permission denied)
        """
        logger.info(f"Deleting agent: {path}")

        self._make_request(method="DELETE", endpoint=f"/api/agents{path}")

        logger.info(f"Agent deleted successfully: {path}")

    def toggle_agent(self, path: str, enabled: bool) -> AgentToggleResponse:
        """
        Toggle agent enabled/disabled status.

        Args:
            path: Agent path
            enabled: True to enable, False to disable

        Returns:
            Agent toggle response

        Raises:
            requests.HTTPError: If toggle fails (404 for not found, 403 for permission denied)
        """
        logger.info(f"Toggling agent {path} to {'enabled' if enabled else 'disabled'}")

        params = {"enabled": str(enabled).lower()}

        response = self._make_request(method="POST", endpoint=f"/api/agents{path}/toggle", params=params)

        result = AgentToggleResponse(**response.json())
        logger.info(f"Agent toggled: {path} is now {'enabled' if result.is_enabled else 'disabled'}")
        return result

    def discover_agents_by_skills(
        self, skills: list[str], tags: list[str] | None = None, max_results: int = 10
    ) -> AgentDiscoveryResponse:
        """
        Discover agents by required skills.

        Args:
            skills: List of required skills
            tags: Optional tag filters
            max_results: Maximum number of results (default: 10, max: 100)

        Returns:
            Agent discovery response

        Raises:
            requests.HTTPError: If discovery fails (400 for bad request)
        """
        logger.info(f"Discovering agents by skills: {skills}")

        request_data = SkillDiscoveryRequest(skills=skills, tags=tags)
        params = {"max_results": max_results}

        response = self._make_request(
            method="POST",
            endpoint="/api/agents/discover",
            data=request_data.model_dump(exclude_none=True),
            params=params,
        )

        result = AgentDiscoveryResponse(**response.json())
        logger.info(f"Discovered {len(result.agents)} agents matching skills")
        return result

    def discover_agents_semantic(self, query: str, max_results: int = 10) -> AgentSemanticDiscoveryResponse:
        """
        Discover agents using semantic search (FAISS vector search).

        Args:
            query: Natural language query (e.g., "Find agents that can analyze code")
            max_results: Maximum number of results (default: 10, max: 100)

        Returns:
            Agent semantic discovery response

        Raises:
            requests.HTTPError: If discovery fails (400 for bad request, 500 for search error)
        """
        logger.info(f"Discovering agents semantically: {query}")

        params = {"query": query, "max_results": max_results}

        response = self._make_request(method="POST", endpoint="/api/agents/discover/semantic", params=params)

        result = AgentSemanticDiscoveryResponse(**response.json())
        logger.info(f"Discovered {len(result.agents)} agents via semantic search")
        return result

    def rate_agent(self, path: str, rating: int) -> RatingResponse:
        """
        Submit a rating for an agent (1-5 stars).

        Each user can only have one active rating. If user has already rated,
        this updates their existing rating. System maintains a rotating buffer
        of the last 100 ratings.

        Args:
            path: Agent path (e.g., /code-reviewer)
            rating: Rating value (1-5 stars)

        Returns:
            Rating response with success message and updated average rating

        Raises:
            requests.HTTPError: If rating fails (400 for invalid rating, 403 for unauthorized, 404 for not found)
        """
        logger.info(f"Rating agent '{path}' with {rating} stars")

        request_data = RatingRequest(rating=rating)

        response = self._make_request(method="POST", endpoint=f"/api/agents{path}/rate", data=request_data.model_dump())

        result = RatingResponse(**response.json())
        logger.info(f"Agent '{path}' rated successfully. New average: {result.average_rating:.2f}")
        return result

    def get_agent_rating(self, path: str) -> RatingInfoResponse:
        """
        Get rating information for an agent.

        Returns average rating and up to 100 most recent individual ratings
        (maintained as rotating buffer).

        Args:
            path: Agent path (e.g., /code-reviewer)

        Returns:
            Rating information with average and individual ratings

        Raises:
            requests.HTTPError: If retrieval fails (403 for unauthorized, 404 for not found)
        """
        logger.info(f"Getting ratings for agent: {path}")

        response = self._make_request(method="GET", endpoint=f"/api/agents{path}/rating")

        result = RatingInfoResponse(**response.json())
        logger.info(
            f"Retrieved ratings for '{path}': {result.num_stars:.2f} stars ({len(result.rating_details)} ratings)"
        )
        return result

    def rescan_agent(self, path: str) -> AgentRescanResponse:
        """
        Trigger a manual security scan for an agent.

        Initiates a new security scan for the specified agent and returns
        the results. This endpoint is useful for re-scanning agents after
        updates or for on-demand security assessments.

        Args:
            path: Agent path (e.g., /code-reviewer)

        Returns:
            Newly generated security scan results

        Raises:
            requests.HTTPError: If scan fails (403 for unauthorized, 404 for not found)
        """
        logger.info(f"Triggering security scan for agent: {path}")

        response = self._make_request(method="POST", endpoint=f"/api/agents{path}/rescan")

        result = AgentRescanResponse(**response.json())
        logger.info(
            f"Security scan completed for '{path}': "
            f"Safe={result.is_safe}, Critical={result.critical_issues}, "
            f"High={result.high_severity}, Medium={result.medium_severity}, "
            f"Low={result.low_severity}"
        )
        return result

    def get_agent_security_scan(self, path: str) -> AgentSecurityScanResponse:
        """
        Get security scan results for an agent.

        Returns the latest security scan results including threat analysis,
        severity levels, and detailed findings from YARA, specification
        validation, and heuristic analyzers.

        Args:
            path: Agent path (e.g., /code-reviewer)

        Returns:
            Security scan results with analysis_results and scan_results

        Raises:
            requests.HTTPError: If retrieval fails (403 for unauthorized, 404 for not found)
        """
        logger.info(f"Getting security scan results for agent: {path}")

        response = self._make_request(method="GET", endpoint=f"/api/agents{path}/security-scan")

        result = AgentSecurityScanResponse(**response.json())
        logger.info(f"Retrieved security scan results for '{path}'")
        return result

    def rate_server(self, path: str, rating: int) -> RatingResponse:
        """
        Submit a rating for a server (1-5 stars).

        Each user can only have one active rating. If user has already rated,
        this updates their existing rating. System maintains a rotating buffer
        of the last 100 ratings.

        Args:
            path: Server path (e.g., /cloudflare-docs)
            rating: Rating value (1-5 stars)

        Returns:
            Rating response with success message and updated average rating

        Raises:
            requests.HTTPError: If rating fails (400 for invalid rating, 403 for unauthorized, 404 for not found)
        """
        logger.info(f"Rating server '{path}' with {rating} stars")

        request_data = RatingRequest(rating=rating)

        response = self._make_request(
            method="POST", endpoint=f"/api/servers{path}/rate", data=request_data.model_dump()
        )

        result = RatingResponse(**response.json())
        logger.info(f"Server '{path}' rated successfully. New average: {result.average_rating:.2f}")
        return result

    def get_server_rating(self, path: str) -> RatingInfoResponse:
        """
        Get rating information for a server.

        Returns average rating and up to 100 most recent individual ratings
        (maintained as rotating buffer).

        Args:
            path: Server path (e.g., /cloudflare-docs)

        Returns:
            Rating information with average and individual ratings

        Raises:
            requests.HTTPError: If retrieval fails (403 for unauthorized, 404 for not found)
        """
        logger.info(f"Getting ratings for server: {path}")

        response = self._make_request(method="GET", endpoint=f"/api/servers{path}/rating")

        result = RatingInfoResponse(**response.json())
        logger.info(
            f"Retrieved ratings for '{path}': {result.num_stars:.2f} stars ({len(result.rating_details)} ratings)"
        )
        return result

    def get_security_scan(self, path: str) -> SecurityScanResult:
        """
        Get security scan results for a server.

        Returns the latest security scan results including threat analysis,
        severity levels, and detailed findings for each tool.

        Args:
            path: Server path (e.g., /cloudflare-docs)

        Returns:
            Security scan results with analysis_results and tool_results

        Raises:
            requests.HTTPError: If retrieval fails (403 for unauthorized, 404 for not found)
        """
        logger.info(f"Getting security scan results for server: {path}")

        response = self._make_request(method="GET", endpoint=f"/api/servers{path}/security-scan")

        result = SecurityScanResult(**response.json())
        logger.info(f"Retrieved security scan results for '{path}'")
        return result

    def rescan_server(self, path: str) -> RescanResponse:
        """
        Trigger a manual security scan for a server.

        Initiates a new security scan for the specified server and returns
        the results. This operation is admin-only.

        Args:
            path: Server path (e.g., /cloudflare-docs)

        Returns:
            Newly generated security scan results

        Raises:
            requests.HTTPError: If scan fails (403 for non-admin, 404 for not found, 500 for scan error)
        """
        logger.info(f"Triggering security scan for server: {path}")

        response = self._make_request(method="POST", endpoint=f"/api/servers{path}/rescan")

        result = RescanResponse(**response.json())
        safety_status = "SAFE" if result.is_safe else "UNSAFE"
        logger.info(
            f"Security scan completed for '{path}': {safety_status} "
            f"(Critical: {result.critical_issues}, High: {result.high_severity}, "
            f"Medium: {result.medium_severity}, Low: {result.low_severity})"
        )
        return result

    # Anthropic Registry API Methods (v0.1)

    def anthropic_list_servers(self, cursor: str | None = None, limit: int | None = None) -> AnthropicServerList:
        """
        List all MCP servers using the Anthropic Registry API format (v0.1).

        This endpoint provides pagination support and returns servers in the
        Anthropic Registry API standard format with reverse-DNS naming.

        Args:
            cursor: Pagination cursor (opaque string from previous response)
            limit: Maximum number of results per page (default: 100, max: 1000)

        Returns:
            Anthropic ServerList with servers and pagination metadata

        Raises:
            requests.HTTPError: If list operation fails
        """
        logger.info("Listing servers via Anthropic Registry API (v0.1)")

        params = {}
        if cursor:
            params["cursor"] = cursor
        if limit:
            params["limit"] = limit

        response = self._make_request(method="GET", endpoint="/v0.1/servers", params=params)

        result = AnthropicServerList(**response.json())
        logger.info(f"Retrieved {len(result.servers)} servers via Anthropic API")
        return result

    def anthropic_list_server_versions(self, server_name: str) -> AnthropicServerList:
        """
        List all versions of a specific server using Anthropic Registry API (v0.1).

        Currently, the registry maintains only one version per server, so this
        returns a single-item list.

        Args:
            server_name: Server name in reverse-DNS format (e.g., "io.mcpgateway/example-server")
                        Will be URL-encoded automatically.

        Returns:
            Anthropic ServerList with single server version

        Raises:
            requests.HTTPError: If server not found (404) or user lacks access (403/404)
        """
        logger.info(f"Listing versions for server: {server_name}")

        # URL-encode the server name
        encoded_name = quote(server_name, safe="")

        response = self._make_request(method="GET", endpoint=f"/v0.1/servers/{encoded_name}/versions")

        result = AnthropicServerList(**response.json())
        logger.info(f"Retrieved {len(result.servers)} version(s) for {server_name}")
        return result

    def anthropic_get_server_version(self, server_name: str, version: str = "latest") -> AnthropicServerResponse:
        """
        Get detailed information about a specific server version using Anthropic Registry API (v0.1).

        Args:
            server_name: Server name in reverse-DNS format (e.g., "io.mcpgateway/example-server")
                        Will be URL-encoded automatically.
            version: Version string (e.g., "1.0.0" or "latest"). Default: "latest"
                    Currently only "latest" and "1.0.0" are supported.

        Returns:
            Anthropic ServerResponse with full server details

        Raises:
            requests.HTTPError: If server not found (404), version not found (404),
                              or user lacks access (403/404)
        """
        logger.info(f"Getting server {server_name} version {version}")

        # URL-encode both server name and version
        encoded_name = quote(server_name, safe="")
        encoded_version = quote(version, safe="")

        response = self._make_request(method="GET", endpoint=f"/v0.1/servers/{encoded_name}/versions/{encoded_version}")

        result = AnthropicServerResponse(**response.json())
        logger.info(f"Retrieved server details for {server_name} v{version}")
        return result

    # Management API Methods (IAM/User Management)

    def list_users(self, search: str | None = None, limit: int = 500) -> UserListResponse:
        """
        List Keycloak users (admin only).

        Args:
            search: Optional search string to filter users
            limit: Maximum number of results (default: 500)

        Returns:
            UserListResponse with list of users

        Raises:
            requests.HTTPError: If not authorized (403) or request fails
        """
        logger.info("Listing Keycloak users")

        params = {}
        if search:
            params["search"] = search
        if limit != 500:
            params["limit"] = limit

        response = self._make_request(method="GET", endpoint="/api/management/iam/users", params=params)

        try:
            response_data = response.json()
            logger.debug(f"Raw API response: {json.dumps(response_data, indent=2, default=str)}")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode JSON response: {e}")
            logger.error(f"Raw response text: {response.text}")
            logger.error(f"Response status code: {response.status_code}")
            logger.error(f"Response headers: {dict(response.headers)}")
            raise

        try:
            result = UserListResponse(**response_data)
            logger.info(f"Retrieved {result.total} users")
            return result
        except Exception as e:
            logger.error(f"Failed to parse user list response: {e}")
            logger.error(f"Raw response data: {json.dumps(response_data, indent=2, default=str)}")
            raise

    def create_m2m_account(self, name: str, groups: list[str], description: str | None = None) -> M2MAccountResponse:
        """
        Create a machine-to-machine service account.

        Args:
            name: Service account name/client ID
            groups: List of group names for access control
            description: Optional account description

        Returns:
            M2MAccountResponse with client credentials

        Raises:
            requests.HTTPError: If not authorized (403), already exists (400), or request fails
        """
        logger.info(f"Creating M2M service account: {name}")

        data = {"name": name, "groups": groups}
        if description:
            data["description"] = description

        response = self._make_request(method="POST", endpoint="/api/management/iam/users/m2m", data=data)

        result = M2MAccountResponse(**response.json())
        logger.info(f"M2M account created successfully: {name}")
        return result

    def create_human_user(
        self, username: str, email: str, first_name: str, last_name: str, groups: list[str], password: str | None = None
    ) -> KeycloakUserSummary:
        """
        Create a human user account in Keycloak.

        Args:
            username: Username
            email: Email address
            first_name: First name
            last_name: Last name
            groups: List of group names
            password: Optional initial password

        Returns:
            KeycloakUserSummary with created user details

        Raises:
            requests.HTTPError: If not authorized (403), already exists (400), or request fails
        """
        logger.info(f"Creating human user: {username}")

        data = {"username": username, "email": email, "firstname": first_name, "lastname": last_name, "groups": groups}
        if password:
            data["password"] = password

        response = self._make_request(method="POST", endpoint="/api/management/iam/users/human", data=data)

        result = KeycloakUserSummary(**response.json())
        logger.info(f"User created successfully: {username}")
        return result

    def delete_user(self, username: str) -> UserDeleteResponse:
        """
        Delete a user by username.

        Args:
            username: Username to delete

        Returns:
            UserDeleteResponse confirming deletion

        Raises:
            requests.HTTPError: If not authorized (403), not found (400/404), or request fails
        """
        logger.info(f"Deleting user: {username}")

        response = self._make_request(method="DELETE", endpoint=f"/api/management/iam/users/{username}")

        result = UserDeleteResponse(**response.json())
        logger.info(f"User deleted successfully: {username}")
        return result

    def list_keycloak_iam_groups(self) -> GroupListResponse:
        """
        List Keycloak IAM groups (admin only).

        This is different from list_groups() which returns groups with server associations.
        This method returns raw Keycloak group data without scopes.

        Returns:
            GroupListResponse with list of groups

        Raises:
            requests.HTTPError: If not authorized (403) or request fails
        """
        logger.info("Listing Keycloak IAM groups")

        response = self._make_request(method="GET", endpoint="/api/management/iam/groups")

        result = GroupListResponse(**response.json())
        logger.info(f"Retrieved {result.total} Keycloak groups")
        return result

    def create_keycloak_group(self, name: str, description: str | None = None) -> KeycloakGroupSummary:
        """
        Create a new Keycloak group (admin only).

        Args:
            name: Group name
            description: Optional group description

        Returns:
            KeycloakGroupSummary with created group details

        Raises:
            requests.HTTPError: If not authorized (403), already exists (400), or request fails
        """
        logger.info(f"Creating Keycloak group: {name}")

        data = {"name": name}
        if description:
            data["description"] = description

        response = self._make_request(method="POST", endpoint="/api/management/iam/groups", data=data)

        result = KeycloakGroupSummary(**response.json())
        logger.info(f"Group created successfully: {name}")
        return result

    def delete_keycloak_group(self, name: str) -> GroupDeleteResponse:
        """
        Delete a Keycloak group by name (admin only).

        Args:
            name: Group name to delete

        Returns:
            GroupDeleteResponse confirming deletion

        Raises:
            requests.HTTPError: If not authorized (403), not found (404), or request fails
        """
        logger.info(f"Deleting Keycloak group: {name}")

        response = self._make_request(method="DELETE", endpoint=f"/api/management/iam/groups/{name}")

        result = GroupDeleteResponse(**response.json())
        logger.info(f"Group deleted successfully: {name}")
        return result
