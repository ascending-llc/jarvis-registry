"""
Discovery schemas for progressive disclosure.

  Level 0 — discover_servers  → raw Weaviate result dicts
  Level 2 — get_server_capabilities → ServerCapabilities
  Level 1 — discover_domains  → DomainResult  (Phase 3)
"""

from pydantic import BaseModel, Field


class ToolSummary(BaseModel):
    """One tool entry inside ServerCapabilities — name and description only, no schema."""

    name: str = Field(description="Tool name, use as-is for execute_tool")
    description: str = Field(default="", description="One-line description")


class ResourceSummary(BaseModel):
    """One resource entry inside ServerCapabilities."""

    name: str = Field(description="Resource name")
    uri: str = Field(default="", description="URI template, use as-is for read_resource")
    description: str = Field(default="", description="One-line description")


class PromptSummary(BaseModel):
    """One prompt entry inside ServerCapabilities."""

    name: str = Field(description="Prompt name, use as-is for execute_prompt")
    description: str = Field(default="", description="One-line description")


class ServerCapabilities(BaseModel):
    """
    Level 2 response: full tool/resource/prompt list for one server.

    Intentionally omits parameter schemas — the LLM selects a tool by name,
    then execute_tool fetches the schema from MongoDB at execution time.
    Token cost: ~20 tokens per tool × N tools (no schema overhead).
    """

    server_name: str
    server_id: str = Field(description="Use this as server_id in execute_tool / read_resource / execute_prompt")
    path: str
    description: str = Field(default="")
    requires_auth: bool = Field(default=False, description="True if server needs OAuth or API key")
    tools: list[ToolSummary] = Field(default_factory=list)
    resources: list[ResourceSummary] = Field(default_factory=list)
    prompts: list[PromptSummary] = Field(default_factory=list)


class DomainResult(BaseModel):
    """One domain/category entry returned by discover_domains."""

    domain: str = Field(description="Domain key, e.g. 'code-management'")
    description: str = Field(description="Human-readable description with representative server names")
    server_names: list[str] = Field(default_factory=list, description="MCP server names in this domain")
    agent_names: list[str] = Field(default_factory=list, description="A2A agent names in this domain")
    total_tools: int = Field(default=0)
    total_skills: int = Field(default=0)
    relevance_score: float = Field(default=0.0, ge=0.0, le=1.0)
