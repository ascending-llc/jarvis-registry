"""
Extended MCP Server Model for Registry-Specific Fields

This module extends the auto-generated MCPServer with registry-specific fields.
The base model (_generated/mcp_server.py) should NOT be modified as it's auto-generated.

Storage Structure (following API documentation specifications):

Configuration Fields (stored in config object):
- title: string - Display name
- description: string - Server description
- type: string - Transport type (streamable-http, sse, stdio, websocket)
- url: string - Server endpoint URL
- apiKey: object (optional) - API key configuration
- requiresOAuth: boolean - Whether OAuth is required
- oauth: object (optional) - OAuth configuration
- capabilities: string - JSON string of server capabilities
- tools: string - Comma-separated list of tool names (e.g., "tool1, tool2, tool3")
- toolFunctions: object - Tool function definitions in OpenAI format with mcpToolName field
- resources: array - List of available MCP resources with uri, name, description, mimeType, annotations
- prompts: array - List of available MCP prompts with name, description, arguments
- initDuration: number - Server initialization time in ms

Identity & Metadata Fields (stored at root level):
- _id (id): ObjectId - MongoDB document ID
- serverName: string - Unique server identifier
- author: ObjectId - User who created this server
- scope: string - Access level (shared_app, shared_user, private_user)
- status: string - Server status (active, inactive, error)
- createdAt: datetime - Creation timestamp
- updatedAt: datetime - Last update timestamp

Additional Fields (stored at root level):
- path: string - API path for this server (e.g., "/mcp/github")
- tags: array[string] - Array of tags for categorization
- numTools: number - Number of tools (calculated from toolFunctions object size)
- numStars: number - Number of stars/favorites
- lastConnected: datetime (nullable) - Last successful connection timestamp
- lastError: datetime (nullable) - Last error timestamp
- errorMessage: string (nullable) - Last error message details

Key Principle:
- Configuration Fields are stored in the config object
- Identity & Metadata and Additional Fields are stored at root level
- numTools is a calculated field, not stored in the database
"""

import hashlib
import logging
from datetime import datetime
from typing import Any, ClassVar

from beanie import Insert, PydanticObjectId, Replace, Save, SaveChanges, Update, before_event
from langchain_core.documents import Document as LangChainDocument
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pydantic import Field
from pymongo import IndexModel

from ..core.config import ChunkingConfig
from ..models.enums import MCPEntityType
from ._generated import MCPServer

logger = logging.getLogger(__name__)


class ExtendedMCPServer(MCPServer):
    """
    Extended MCP Server Document with Registry-Specific Fields

    This model extends the base MCPServer with registry-specific fields
    that are stored at root level in MongoDB, not in the config object.

    Storage Structure (MongoDB):
    {
      "_id": ObjectId("..."),
      "serverName": "github",
      "config": {  # MCP-specific configuration
        "title": "GitHub MCP Server",
        "description": "...",
        "type": "streamable-http",
        "url": "http://github-server:8011",
        "apiKey": {...} or "oauth": {...} or "authentication": {...},
        "requiresOAuth": false,
        "capabilities": "{}",  # JSON string
        "toolFunctions": {     # OpenAI function format with mcpToolName
          "tool1_mcp_github": {
            "type": "function",
            "function": {
              "name": "tool1_mcp_github",
              "description": "...",
              "parameters": {...}
            },
            "mcpToolName": "tool1"  # Original MCP tool name
          }
        },
        "resources": [         # MCP resources
          {
            "uri": "github://repo/{owner}/{repo}",
            "name": "repository",
            "description": "...",
            "mimeType": "application/json",
            "annotations": {...}
          }
        ],
        "prompts": [           # MCP prompts
          {
            "name": "code_review",
            "description": "...",
            "arguments": [...]
          }
        ],
        "tools": "tool1, tool2",
        "initDuration": 170
      },
      "scope": "shared_app",  # Registry field (root level)
      "status": "active",     # Registry field (root level)
      "path": "/mcp/github",  # Registry field (root level)
      "tags": ["github"],     # Registry field (root level)
      "numTools": 2,          # Registry field (root level)
      "numStars": 0,          # Registry field (root level)
      "lastConnected": ISODate("..."),  # Registry field (root level)
      "lastError": ISODate("..."),      # Registry field (root level)
      "errorMessage": "...",   # Registry field (root level)
      "author": ObjectId("..."),
      "createdAt": ISODate("..."),
      "updatedAt": ISODate("...")
    }
    """

    # ========== Base Fields from MCPServer ==========
    # We use the following fields inherited from the generated class MCPServer:
    # - serverName: str - Server name for display
    # - config: dict[str, Any] - MCP server configuration (oauth, apiKey, capabilities, tools, etc).
    # - author: PydanticObjectId - User who created this server
    # - createdAt: Optional[datetime] - auto-generated by Beanie
    # - updatedAt: Optional[datetime] - auto-generated by Beanie
    #
    # We don't use the inherited `tenantId` field.

    # ========== Registry-Specific Root-Level Fields ==========
    # These fields are specific to the registry and should NOT be in config
    path: str | None = Field(default=None, description="API path for this server (e.g., /mcp/github)")
    tags: list[str] = Field(default_factory=list, description="Tags for categorization")
    scope: str | None = Field(default=None, description="Deprecated. Access control is handled by ACL permissions.")
    status: str = Field(default="active", description="Operational state: active, inactive, error")
    numTools: int = Field(default=0, alias="numTools", description="Number of tools (calculated from toolFunctions)")
    numStars: int = Field(default=0, alias="numStars", description="Number of stars/favorites")

    # Monitoring fields
    lastConnected: datetime | None = Field(
        default=None, alias="lastConnected", description="Last successful connection timestamp"
    )
    lastError: datetime | None = Field(default=None, alias="lastError", description="Last error timestamp")
    errorMessage: str | None = Field(default=None, alias="errorMessage", description="Last error message details")

    federationRefId: PydanticObjectId | None = None
    federationMetadata: dict[str, Any] | None = None

    vectorContentHash: str | None = Field(
        default=None,
        description="SHA-256 of vectorized page_content; used to skip re-embedding when content is unchanged",
    )

    class Settings:
        name = "mcpservers"
        keep_nulls = False
        use_state_management = True

        # NOTE: We MUST NOT repeat indices already defined in jarvis-api or define an index that conflicts with it.
        # Better not to mention any fields defined by jarvis-api. It's safe to define indices on our own fields.
        indexes = [
            IndexModel([("federationRefId", 1)]),
            IndexModel([("federationMetadata.runtimeArn", 1)], sparse=True),
        ]

    @before_event(Insert, Replace, Save, SaveChanges, Update)
    def _refresh_content_hash(self):
        """Recompute vectorContentHash before every write.

        Service layer captures the hash before .save() and compares after to decide whether to
        call sync_to_vector_db (full rebuild) or update_entity_metadata (metadata-only patch).
        This contract holds as long as enabled/status are NOT included in page_content — if
        to_documents() ever embeds those fields, toggle paths will incorrectly trigger full syncs.
        """
        docs = self.to_documents()
        contents = sorted(doc.page_content for doc in docs)
        self.vectorContentHash = hashlib.sha256("\n---\n".join(contents).encode()).hexdigest()

    # ========== Vector Search Integration (Weaviate) ==========
    COLLECTION_NAME: ClassVar[str] = "MCP_Servers"

    def to_documents(self, chunking_config: ChunkingConfig | None = None) -> list[LangChainDocument]:
        """
        Convert ExtendedMCPServer to searchable vector documents.

        Each tool/resource/prompt document embeds the server context (name, path, title,
        description) as a prefix in its content, making every document self-contained for
        vector search. This eliminates the need for a separate server-level document and
        allows all discovery paths — including server-level queries — to be served directly
        from tool/resource/prompt docs without a MongoDB lookup.

        Returns:
            List of LangChain Documents with entity_type metadata (tool, resource, prompt)
        """
        docs = []
        chunking_config = chunking_config or ChunkingConfig()

        # Tools
        tool_functions = self.config.get("toolFunctions", {})
        for tool_name, tool_data in tool_functions.items():
            tool_docs = self._create_tool_docs(tool_name, tool_data, chunking_config)
            docs.extend(tool_docs)

        # Resources
        resources = self.config.get("resources", [])
        for resource in resources:
            resource_docs = self._create_resource_docs(resource, chunking_config)
            docs.extend(resource_docs)

        # Prompts
        prompts = self.config.get("prompts", [])
        for prompt in prompts:
            prompt_docs = self._create_prompt_docs(prompt, chunking_config)
            docs.extend(prompt_docs)

        logger.info(
            f"Generated {len(docs)} documents for server {self.serverName} "
            f"(tools:{len(tool_functions)}, resources:{len(resources)}, prompts:{len(prompts)})"
        )

        return docs

    def _create_tool_docs(
        self, tool_name: str, tool_data: dict, chunking_config: ChunkingConfig
    ) -> list[LangChainDocument]:
        """Create Tool document(s) with text splitting if needed."""
        downstream_tool_name = tool_data.get("mcpToolName", tool_name)
        content = self.generate_tool_content(downstream_tool_name, tool_data)

        metadata = self._get_base_metadata(MCPEntityType.TOOL)
        metadata["tool_name"] = downstream_tool_name
        # Store the full parameter schema so LLMs can execute without a separate lookup.
        func = tool_data.get("function", {}) if isinstance(tool_data, dict) else {}
        input_schema = func.get("parameters")
        if input_schema:
            metadata["input_schema"] = input_schema

        return self._split_if_needed(content, metadata, chunking_config)

    def _create_resource_docs(self, resource: dict, chunking_config: ChunkingConfig) -> list[LangChainDocument]:
        """Create Resource document(s) with text splitting if needed."""
        content = self.generate_resource_content(resource)

        metadata = self._get_base_metadata(MCPEntityType.RESOURCE)
        metadata.update({"resource_name": resource.get("name", ""), "resource_uri": resource.get("uri", "")})

        return self._split_if_needed(content, metadata, chunking_config)

    def _create_prompt_docs(self, prompt: dict, chunking_config: ChunkingConfig) -> list[LangChainDocument]:
        """Create Prompt document(s) with text splitting if needed."""
        content = self.generate_prompt_content(prompt)

        metadata = self._get_base_metadata(MCPEntityType.PROMPT)
        metadata.update({"prompt_name": prompt.get("name", "")})

        return self._split_if_needed(content, metadata, chunking_config)

    def _get_base_metadata(self, entity_type: MCPEntityType) -> dict[str, Any]:
        """Get base metadata shared by all document types."""
        is_enabled = self.status == "active"
        if self.config and isinstance(self.config.get("enabled"), bool):
            is_enabled = self.config["enabled"]

        metadata = {
            "collection": self.COLLECTION_NAME,
            "entity_type": entity_type,
            "server_id": str(self.id) if self.id else None,
            "server_name": self.serverName,
            "path": self.path,
            "status": self.status,
            "enabled": is_enabled,
        }
        # Federation metadata lets vector sync target one federated MCP runtime precisely.
        if self.federationRefId is not None:
            metadata["federation_id"] = str(self.federationRefId)
        runtime_version = (self.federationMetadata or {}).get("runtimeVersion")
        if runtime_version is not None:
            metadata["runtimeVersion"] = str(runtime_version)
        # Keep runtimeArn for debugging and future runtime-scoped repair.
        runtime_arn = (self.federationMetadata or {}).get("runtimeArn")
        if runtime_arn:
            metadata["runtimeArn"] = runtime_arn
        if self.tags:
            metadata["tags"] = list(self.tags)
        return metadata

    def mutable_metadata(self) -> dict[str, Any]:
        """Return metadata fields that can change without affecting page_content.

        serverName and path are intentionally excluded: both appear in page_content
        (as doc prefix), so changing either always changes vectorContentHash and
        triggers a full rebuild — they never reach this path.
        """
        is_enabled = self.status == "active"
        if self.config and isinstance(self.config.get("enabled"), bool):
            is_enabled = self.config["enabled"]
        meta: dict[str, Any] = {
            "status": self.status,
            "enabled": is_enabled,
            "tags": list(self.tags) if self.tags else [],
        }
        runtime_version = (self.federationMetadata or {}).get("runtimeVersion")
        if runtime_version is not None:
            meta["runtimeVersion"] = str(runtime_version)
        return meta

    def _split_if_needed(
        self, content: str, metadata: dict[str, Any], chunking_config: ChunkingConfig
    ) -> list[LangChainDocument]:
        """
        Split content if it exceeds MAX_CHUNK_SIZE using RecursiveCharacterTextSplitter.

        Args:
            content: Original content
            metadata: Base metadata to attach to all chunks

        Returns:
            List of LangChain Documents (1 if no split needed, N if split)
        """
        if len(content) <= chunking_config.max_chunk_size:
            return [LangChainDocument(page_content=content, metadata=metadata)]

        # Split required
        entity_identifier = metadata.get("tool_name") or metadata.get("server_name") or "unknown"
        logger.warning(
            f"Content exceeds {chunking_config.max_chunk_size} chars ({len(content)} chars), splitting... "
            f"[{metadata.get('entity_type')}: {entity_identifier}]"
        )

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunking_config.max_chunk_size,
            chunk_overlap=chunking_config.chunk_overlap,
            separators=["\n## ", "\n### ", "\n\n", "\n", " | ", " ", ""],
            length_function=len,
        )

        chunks = splitter.split_text(content)

        docs = []
        for i, chunk in enumerate(chunks):
            chunk_metadata = metadata.copy()
            chunk_metadata.update({"chunk_index": i, "total_chunks": len(chunks), "is_chunked": True})
            docs.append(LangChainDocument(page_content=chunk, metadata=chunk_metadata))

        logger.info(f"Split into {len(chunks)} chunks")
        return docs

    def _server_prefix(self) -> str:
        """
        Build the shared server context prefix embedded in every tool/resource/prompt document.

        Embedding this prefix makes each document self-contained so that vector search
        on tool/resource/prompt docs can match server-level terms (e.g. "github") without
        requiring a separate MongoDB lookup.

        Format: serverName | path | title | description
        """
        return " | ".join(
            filter(
                None,
                [
                    self.serverName,
                    self.path,
                    self.config.get("title", ""),
                    self.config.get("description", ""),
                ],
            )
        )

    def generate_tool_content(self, tool_name: str, tool_data: dict) -> str:
        """
        Generate content for Tool document.

        Format: serverName | path | title | description |
                tool_name | description |
                Parameters: param1 (type, required/optional, description), ...

        The server prefix makes tool docs self-contained for vector search,
        eliminating the need for a MongoDB lookup during server discovery.
        """
        parts = [tool_name]

        if isinstance(tool_data, dict) and "function" in tool_data:
            func = tool_data["function"]

            # Description
            description = func.get("description", "")
            if description:
                parts.append(description)

            # Parameters
            params = func.get("parameters", {})
            if params and "properties" in params:
                param_strs = []
                required_params = params.get("required", [])

                for param_name, param_schema in params["properties"].items():
                    param_type = param_schema.get("type", "unknown")
                    param_desc = param_schema.get("description", "")
                    required = "required" if param_name in required_params else "optional"

                    # Truncate long descriptions
                    if len(param_desc) > 200:
                        param_desc = param_desc[:197] + "..."

                    param_str = f"{param_name} ({param_type}, {required}"
                    if param_desc:
                        param_str += f", {param_desc}"
                    param_str += ")"

                    param_strs.append(param_str)

                if param_strs:
                    parts.append(f"Parameters: {', '.join(param_strs)}")

        tool_body = " | ".join(filter(None, parts))
        return f"{self._server_prefix()} | {tool_body}"

    def generate_resource_content(self, resource: dict) -> str:
        """
        Generate content for Resource document.

        Format: serverName | path | title | description |
                name | description | URI: uri_template | MIME type: mime_type

        The server prefix makes resource docs self-contained for vector search.
        """
        name = resource.get("name", "")
        description = resource.get("description", "")
        uri = resource.get("uri", "")
        mime_type = resource.get("mimeType", "")

        parts = [name, description]

        if uri:
            parts.append(f"URI template: {uri}")

        if mime_type:
            parts.append(f"MIME type: {mime_type}")

        resource_body = " | ".join(filter(None, parts))
        return f"{self._server_prefix()} | {resource_body}"

    def generate_prompt_content(self, prompt: dict) -> str:
        """
        Generate content for Prompt document.

        Format: serverName | path | title | description |
                name | description | Required: required_args | Optional: optional_args

        The server prefix makes prompt docs self-contained for vector search.
        """
        name = prompt.get("name", "")
        description = prompt.get("description", "")
        arguments = prompt.get("arguments", [])

        parts = [name, description]

        # Separate required and optional arguments
        required_args = []
        optional_args = []

        for arg in arguments:
            arg_name = arg.get("name", "")
            arg_desc = arg.get("description", "")
            arg_str = f"{arg_name} ({arg_desc})" if arg_desc else arg_name

            if arg.get("required", False):
                required_args.append(arg_str)
            else:
                optional_args.append(arg_str)

        if required_args:
            parts.append(f"Required: {', '.join(required_args)}")
        if optional_args:
            parts.append(f"Optional: {', '.join(optional_args)}")

        prompt_body = " | ".join(filter(None, parts))
        return f"{self._server_prefix()} | {prompt_body}"

    @classmethod
    def from_document(cls, document: LangChainDocument) -> dict:
        """
        Extract metadata from any document type.

        Returns execution-ready fields for LLM consumption.
        relevance_score is populated by the reranker (FlashRank sets it in metadata);
        None for filter-only results where no semantic score is available.
        """
        metadata = document.metadata

        raw_score = metadata.get("relevance_score")
        result = {
            "server_id": metadata.get("server_id"),
            "server_name": metadata.get("server_name"),
            "entity_type": metadata.get("entity_type"),
            "relevance_score": round(float(raw_score), 3) if raw_score is not None else None,
            "description": document.page_content,
        }

        entity_type = metadata.get("entity_type")
        if entity_type == MCPEntityType.TOOL:
            result["tool_name"] = metadata.get("tool_name")
            result["input_schema"] = metadata.get("input_schema")
        elif entity_type == MCPEntityType.RESOURCE:
            result["resource_name"] = metadata.get("resource_name")
            result["resource_uri"] = metadata.get("resource_uri")
        elif entity_type == MCPEntityType.PROMPT:
            result["prompt_name"] = metadata.get("prompt_name")

        return result

    @classmethod
    def from_server_info(cls, server_info: dict[str, Any], is_enabled: bool = False) -> "ExtendedMCPServer":
        """
        Create ExtendedMCPServer instance from server info dictionary.

        Args:
            server_info: Server information dictionary (must contain 'path' and 'server_name')
            is_enabled: Whether the service is enabled (maps to status)

        Returns:
            ExtendedMCPServer instance

        Raises:
            ValueError: If required fields are missing
        """
        # Extract required fields
        path = server_info.get("path")
        if not path:
            raise ValueError("server_info must contain 'path' field")

        server_name = server_info.get("server_name", path.strip("/"))
        config = server_info.get("config", {})

        # If config is not provided, build it from server_info
        if not config:
            config = {
                "title": server_info.get("title", server_name),
                "description": server_info.get("description", ""),
                "toolFunctions": server_info.get("toolFunctions", {}),
                "resources": server_info.get("resources", []),
                "prompts": server_info.get("prompts", []),
            }

        # Map is_enabled to status
        status = "active" if is_enabled else "inactive"

        # Extract server_id if available (for updates)
        server_id = server_info.get("id") or server_info.get("_id")

        # Create server instance
        return cls(
            id=PydanticObjectId(server_id) if server_id else None,
            serverName=server_name,
            path=path,
            config=config,
            status=status,
            tags=server_info.get("tags", []),
            author=server_info.get("author") or PydanticObjectId(),
            federationRefId=server_info.get("federationRefId"),
            federationMetadata=server_info.get("federationMetadata"),
        )
