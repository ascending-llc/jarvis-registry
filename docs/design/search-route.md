### Unified Semantic Search

**Endpoint**: `POST /api/v1/search`

**Request Body**:
```json
{
  "query": "github code search integration",
  "entityTypes": ["mcp_server", "tool", "a2a_agent", "skill"],
  "maxResults": 10,
  "includeDisabled": false
}
```

**Request Fields**:
- `query` (required, string): Natural language search query. Min 1 character, max 512 characters.
- `entityTypes` (optional, array of strings): Subset of entity types to search. Omit to search all four types.
  - `"mcp_server"` — MCP server entries
  - `"tool"` — individual MCP tools
  - `"a2a_agent"` — A2A agents
  - `"skill"` — individual A2A agent skills
- `maxResults` (optional, number): Maximum results returned **per entity collection** (default: `10`, min: `1`, max: `50`). Each of `servers`, `tools`, `agents`, and `skills` is capped independently at this value.
- `includeDisabled` (optional, boolean): When `true`, includes disabled agents and skills in results (default: `false`).

**Response**: `200 OK`
```json
{
  "query": "github code search integration",
  "servers": [
    {
      "path": "/github",
      "serverName": "GitHub",
      "description": "GitHub integration for repositories, issues, and pull requests",
      "tags": ["github", "code"],
      "numTools": 12,
      "isEnabled": true,
      "relevanceScore": 0.94,
      "matchContext": "GitHub integration service for managing repositories",
      "matchingTools": [
        {
          "toolName": "search_code",
          "description": "Search code across GitHub repositories",
          "relevanceScore": 0.91,
          "matchContext": "Search code across GitHub repositories"
        }
      ]
    }
  ],
  "tools": [
    {
      "serverPath": "/github",
      "serverName": "GitHub",
      "toolName": "search_code",
      "description": "Search code across GitHub repositories",
      "relevanceScore": 0.91,
      "matchContext": "Search code across GitHub repositories"
    }
  ],
  "agents": [
    {
      "agentId": "agent-abc123",
      "path": "/code-review-agent",
      "agentName": "Code Review Agent",
      "description": "Automated code review and quality analysis",
      "tags": ["code", "review"],
      "isEnabled": true,
      "relevanceScore": 0.87,
      "matchContext": "Automated code review and quality analysis"
    }
  ],
  "skills": [
    {
      "agentId": "agent-abc123",
      "agentPath": "/code-review-agent",
      "agentName": "Code Review Agent",
      "skillName": "analyze_diff",
      "description": "Analyzes a code diff for issues and suggestions",
      "relevanceScore": 0.83,
      "matchContext": "Analyzes a code diff for issues and suggestions"
    }
  ],
  "totalServers": 1,
  "totalTools": 1,
  "totalAgents": 1,
  "totalSkills": 1
}
```

**Response Fields**:
- `query` (string): The search query, whitespace-trimmed.
- `servers` (array): Matching MCP servers. Empty array if `entityTypes` excludes `"mcp_server"` or no matches found.
- `tools` (array): Matching MCP tools. Empty array if `entityTypes` excludes `"tool"` or no matches found.
- `agents` (array): Matching A2A agents. Empty array if `entityTypes` excludes `"a2a_agent"` or no matches found.
- `skills` (array): Matching A2A skills. Empty array if `entityTypes` excludes `"skill"` or no matches found.
- `totalServers` / `totalTools` / `totalAgents` / `totalSkills` (number): Count of items in the corresponding array.

**`ServerSearchResult` fields**:
- `path` (string): Server mount path, e.g. `"/github"`.
- `serverName` (string): Display name of the server.
- `description` (string | null): Server description.
- `tags` (array of strings): Tags associated with the server.
- `numTools` (number): Total number of tools the server exposes.
- `isEnabled` (boolean): Whether the server is currently enabled.
- `relevanceScore` (number): Semantic relevance score, `0.0`–`1.0`.
- `matchContext` (string | null): Excerpt explaining why this result matched the query.
- `matchingTools` (array): Tools within this server that also matched the query (may be empty). Each entry:
  - `toolName` (string)
  - `description` (string | null)
  - `relevanceScore` (number, `0.0`–`1.0`)
  - `matchContext` (string | null)

**`ToolSearchResult` fields**:
- `serverPath` (string): Mount path of the server that owns this tool.
- `serverName` (string): Display name of the owning server.
- `toolName` (string): Name of the tool.
- `description` (string | null): Tool description.
- `relevanceScore` (number): Semantic relevance score, `0.0`–`1.0`.
- `matchContext` (string | null): Excerpt explaining why this result matched the query.

**`AgentSearchResult` fields**:
- `agentId` (string | null): Unique agent identifier (may be null for legacy entries).
- `path` (string): Agent endpoint path.
- `agentName` (string): Display name of the agent.
- `description` (string | null): Agent description.
- `tags` (array of strings): Tags associated with the agent.
- `isEnabled` (boolean): Whether the agent is currently enabled.
- `relevanceScore` (number): Semantic relevance score, `0.0`–`1.0`.
- `matchContext` (string | null): Excerpt explaining why this result matched the query.

**`SkillSearchResult` fields**:
- `agentId` (string | null): Unique identifier of the owning agent (may be null for legacy entries).
- `agentPath` (string): Endpoint path of the owning agent.
- `agentName` (string): Display name of the owning agent.
- `skillName` (string): Name of the skill.
- `description` (string | null): Skill description.
- `relevanceScore` (number): Semantic relevance score, `0.0`–`1.0`.
- `matchContext` (string | null): Excerpt explaining why this result matched the query.

**Errors**:
- `400` Invalid request (e.g. empty `query`, `query` exceeds 512 characters, `maxResults` out of range)
- `401` Not authenticated
- `503` Search backend temporarily unavailable — retry with exponential backoff
