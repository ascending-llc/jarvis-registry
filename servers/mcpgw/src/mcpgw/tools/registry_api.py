"""
MCP Gateway Registry API Tools
- execute_tool: Execute tools from any MCP server
- read_resource: Read/access resources from any MCP server
- execute_prompt: Execute prompts from any MCP server
"""

import logging
from collections.abc import Callable
from typing import Any

from fastmcp import Context
from pydantic import Field

from ..core.registry import call_registry_api

logger = logging.getLogger(__name__)


async def execute_tool_impl(
    ctx: Context, server_path: str, tool_name: str, arguments: dict[str, Any], server_id: str
) -> dict[str, Any]:
    """
    Execute a specific tool (implementation layer).

    Args:
        server_path: Server path from discovery (e.g., '/tavilysearch')
        tool_name: Resolved tool name to send to MCP server (e.g., 'tavily_search')
        arguments: Tool-specific arguments as key-value pairs
        server_id: Server ID from discovery (e.g., '6972e222755441652c23090f')
        ctx: FastMCP context with user auth

    Returns:
        Tool execution result

    Note:
        This is the implementation layer - tool_name should already be resolved
        by the interface layer (execute_tool). This function just sends the
        request to the registry API.
    """
    logger.info(f"🔧 Executing tool: {tool_name} on server_path: {server_path}, server_id: {server_id}")

    try:
        # Build request payload
        payload = {"server_path": server_path, "tool_name": tool_name, "arguments": arguments}
        payload["server_id"] = server_id

        # Use centralized registry API call with automatic auth header extraction
        result = await call_registry_api(ctx, method="POST", endpoint="/proxy/tools/call", payload=payload)

        logger.info(f"✅ Tool execution successful: {tool_name}")
        return result

    except Exception as e:
        logger.error(f"❌ Tool execution failed: {e}")
        raise Exception(f"Tool execution failed: {str(e)}")


async def read_resource_impl(ctx: Context, server_id: str, resource_uri: str) -> dict[str, Any]:
    """
    Read/access a resource from an MCP server.

    Args:
        server_id: Server ID from discovery
        resource_uri: Resource URI to read (e.g., 'tavily://search-results/AI')
        ctx: FastMCP context with user auth

    Returns:
        Resource contents (text, JSON, binary, etc.)

    Example:
        result = await read_resource_impl(
            server_id="6972e222755441652c23090f",
            resource_uri="tavily://search-results/AI"
        )
    """
    logger.info(f"📄 Reading resource: {resource_uri} from server {server_id}")

    try:
        # Build request payload
        payload = {"server_id": server_id, "resource_uri": resource_uri}

        # Use centralized registry API call with automatic auth header extraction
        result = await call_registry_api(ctx, method="POST", endpoint="/proxy/resources/read", payload=payload)

        logger.info(f"✅ Resource read successful: {resource_uri}")
        return result

    except Exception as e:
        logger.error(f"❌ Resource read failed: {e}")
        raise Exception(f"Resource read failed: {str(e)}")


async def execute_prompt_impl(
    ctx: Context, server_id: str, prompt_name: str, arguments: dict[str, Any] | None = None
) -> dict[str, Any]:
    """
    Execute a prompt from an MCP server.

    Args:
        server_id: Server ID from discovery
        prompt_name: Name of the prompt to execute
        arguments: Optional prompt arguments
        ctx: FastMCP context with user auth

    Returns:
        Prompt messages ready for LLM consumption

    Example:
        result = await execute_prompt_impl(
            server_id="6972e222755441652c23090f",
            prompt_name="research_assistant",
            arguments={
                "topic": "Artificial Intelligence",
                "depth": "comprehensive"
            }
        )
    """
    logger.info(f"💬 Executing prompt: {prompt_name} on server {server_id}")

    try:
        # Build request payload
        payload = {"server_id": server_id, "prompt_name": prompt_name, "arguments": arguments or {}}

        # Use centralized registry API call with automatic auth header extraction
        result = await call_registry_api(ctx, method="POST", endpoint="/proxy/prompts/execute", payload=payload)

        logger.info(f"✅ Prompt execution successful: {prompt_name}")
        return result

    except Exception as e:
        logger.error(f"❌ Prompt execution failed: {e}")
        raise Exception(f"Prompt execution failed: {str(e)}")


# ============================================================================
# Tool Factory Functions for Registration
# ============================================================================


def get_tools() -> list[tuple[str, Callable]]:
    """
    Export tools for registration in server.py.

    Returns:
        List of (tool_name, tool_function) tuples ready for registration
    """

    # Define tool wrapper function with proper signature and decorators
    async def execute_tool(
        ctx: Context,
        server_path: str = Field(..., description="Server path from discovery (e.g., '/tavilysearch' for web search)"),
        tool_name: str = Field(
            ..., description="Tool name from discovery - can be scoped name (e.g., 'tavily_search_mcp_tavily_search')"
        ),
        arguments: dict[str, Any] = Field(..., description="Tool parameters from input_schema"),
        server_id: str = Field(..., description="Server ID from discovery"),
        mcp_tool_name: str | None = Field(
            None,
            description="Original MCP tool name (e.g., 'tavily_search') - extract from toolFunction.mcpToolName if available",
        ),
    ) -> dict[str, Any]:
        """
        🚀 AUTO-USE: Execute any discovered tool to get real-time data.

        **Common Examples:**
        ```
        # Web search
        execute_tool(
            server_path="/tavilysearch",
            tool_name="tavily_search",
            mcp_tool_name="tavily_search",  # Extract from toolFunction.mcpToolName
            arguments={"query": "latest AI news", "max_results": 5},
            server_id="6972e222755441652c23090f"
        )

        # GitHub operations
        execute_tool(
            server_path="/github",
            tool_name="search_pull_requests",
            arguments={"owner": "org", "repo": "project", "state": "open"},
            server_id="abc123..."
        )
        ```

        **Parameters:**
        - server_path: From discovery (e.g., "/tavilysearch")
        - tool_name: Tool identifier (can be scoped name like "tavily_search_mcp_tavily_search")
        - mcp_tool_name: Original MCP name from toolFunction.mcpToolName (e.g., "tavily_search")
        - arguments: Tool-specific parameters from input_schema
        - server_id: Server ID from discovery

        **Note:** If mcp_tool_name is provided, it's used for execution (MCP standard).
        Otherwise, tool_name is used directly.

        ⚠️ Use after discover_servers to execute tools.
        Returns: Tool-specific results (format varies by tool)
        """
        # Resolve tool name at interface layer: use mcpToolName if provided, otherwise use tool_name
        resolved_tool_name = mcp_tool_name or tool_name

        # Log the resolution for debugging
        if mcp_tool_name and mcp_tool_name != tool_name:
            logger.debug(f"Resolved tool name: '{resolved_tool_name}' (from mcpToolName, scoped was '{tool_name}')")

        return await execute_tool_impl(ctx, server_path, resolved_tool_name, arguments, server_id)

    async def read_resource(
        ctx: Context,
        server_id: str = Field(..., description="Server ID from discover_servers (e.g., '6972e222755441652c23090f')"),
        resource_uri: str = Field(
            ..., description="Resource URI to read (e.g., 'tavily://search-results/AI', 'file:///path/to/data')"
        ),
    ) -> dict[str, Any]:
        """
        📄 Read/access resources from any MCP server.

        **What are resources?**
        Resources are data sources, caches, URIs, or file-like objects exposed by MCP servers.
        Examples: cached search results, configuration files, data streams, API responses.

        **Common use cases:**
        - Access cached data: read_resource(server_id="...", resource_uri="tavily://search-results/AI")
        - Read configuration: read_resource(server_id="...", resource_uri="config://settings")
        - Access files: read_resource(server_id="...", resource_uri="file:///data/export.json")

        **Workflow:**
        1. Use discover_servers to find servers with resources
        2. Review the resources array in server config
        3. Use read_resource with the resource URI

        **Example:**
        ```
        # Discover servers with resources
        servers = discover_servers(query="search")

        # Find a resource URI from server config
        resource = servers[0]["config"]["resources"][0]["uri"]

        # Read the resource
        data = read_resource(
            server_id=servers[0]["_id"],
            resource_uri=resource
        )
        ```

        Returns: Resource contents (format varies: text, JSON, binary, etc.)
        """
        return await read_resource_impl(ctx, server_id, resource_uri)

    async def execute_prompt(
        ctx: Context,
        server_id: str = Field(..., description="Server ID from discover_servers"),
        prompt_name: str = Field(
            ..., description="Name of the prompt to execute (e.g., 'research_assistant', 'fact_checker')"
        ),
        arguments: dict[str, Any] | None = Field(None, description="Prompt arguments as key-value pairs"),
    ) -> dict[str, Any]:
        """
        💬 Execute prompts from any MCP server.

        **What are prompts?**
        Prompts are pre-configured, reusable prompt templates provided by MCP servers.
        They help standardize complex workflows and provide expert guidance.

        **Common use cases:**
        - Research workflows: execute_prompt(server_id="...", prompt_name="research_assistant", arguments={"topic": "AI"})
        - Fact checking: execute_prompt(server_id="...", prompt_name="fact_checker", arguments={"claim": "..."})
        - Code review: execute_prompt(server_id="...", prompt_name="code_reviewer", arguments={"language": "python"})

        **Workflow:**
        1. Use discover_servers to find servers with prompts
        2. Review the prompts array in server config
        3. Use execute_prompt with required arguments

        **Example:**
        ```
        # Discover servers with prompts
        servers = discover_servers(query="research")

        # Find available prompts
        prompts = servers[0]["config"]["prompts"]

        # Execute a prompt
        result = execute_prompt(
            server_id=servers[0]["_id"],
            prompt_name="research_assistant",
            arguments={"topic": "Quantum Computing", "depth": "advanced"}
        )

        # Result contains messages ready for LLM
        messages = result["messages"]
        ```

        Returns: Prompt messages ready for LLM consumption (role, content pairs)
        """
        return await execute_prompt_impl(ctx, server_id, prompt_name, arguments)

    # Return list of (name, function) tuples
    return [
        ("execute_tool", execute_tool),
        ("read_resource", read_resource),
        ("execute_prompt", execute_prompt),
    ]
