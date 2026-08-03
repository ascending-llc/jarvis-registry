---
title: "🚀 Jarvis Registry asc0.5.1"
description: "The asc0.5.1 release of Jarvis Registry"
date: 2026-07-06
tags:
  - Release
---

[← Back to changelog](index.md)

# 🚀 Jarvis Registry asc0.5.1

_July 06, 2026_ · [asc0.5.1 on GitHub](https://github.com/ascending-llc/jarvis-registry/releases/tag/asc0.5.1)

---

### ✨ Features

- Add workflow creation and update APIs with referencedNodeNames field support. (#442)
- Add GroupMember.Read.All and User.ReadBasic.All application type support. (#441)
- Remove workflow type primitive and merge gate nodes into the step humanReview node. (#438)
- Ensure ACL lookup failures return 503 Service Unavailable instead of propagating errors. (#437)
- Enable group-based ACL resolution by detecting user group memberships and including group principals in ACL queries. (#434)
- Add workflow replay, node rerun, and trigger run modal with initial input support. (#435)
- Enforce MCP server ACL VIEW permission checks on tool execution and direct-connect proxy handlers. (#433)
- Allow native-app custom redirect URI schemes (e.g., vscode://) while blocking dangerous schemes; return RFC 7591 OAuth error shapes for DCR registration errors. (#431)
- Replace short-lived confirmation tokens with standard OAuth access and refresh token flow with rotating token pairs and configurable expiration. (#428)
- Add HMAC-based CSRF protection for session requests to prevent token reuse attacks. (#429)
- Add Redis integration to the auth-server for session and token storage. (#416)
- Add downstream OAuth flow endpoints for per-user authentication on direct-connect proxy URLs. (#420)
- Add serverId to search results. (#414)
- Introduce separate JWT token classes for CRUD operations and managed agent proxy paths to prevent cross-route token reuse. (#399)
- Add workflow enable/disable controls and create workflows in disabled state by default. (#361)
- Add workflow ACL resource type with sharing scope, implement workflow versioning with auto-increment on updates, and add approval gate nodes for human-in-the-loop workflows. (#364)
- Add comprehensive workflow run API endpoints for triggering, polling status, drilling into node I/O, reruns, replays, and lineage tracking. (#418)
- Use enabled boolean for agent and MCP status instead of health states and make add nodes selectable. (#391)
- Support AWS Quick Suite MCP registration with OAuth token fallback to parse Basic Auth credentials. (#385)
- Add global Add Node button to workflow canvas with automatic placeholder replenishment on node deletion. (#384)
- Add delete handlers for workflow canvas nodes with automatic placeholder replenishment. (#380)
- Introduce structured multi-part message format for A2A agent execution with TextPart, DataPart, and FilePart validation. (#373)
- Replace health status with enabled/disabled toggle and add agent sync button. (#374)
- Reorder search results by viewMode and improve agent name display. (#405)
- Integrate workflow canvas, API, dashboard list, and edit/run flows with read-only mode and position persistence. (#343)

### 🐛 Bug Fixes

- Increase Nginx response header buffer size to 64KB to prevent 502 errors from large response headers. (#443)
- Fix AgentCore A2A update authentication and URL fallback for federated agents. (#439)
- Allow A2A client to negotiate transport when card configuration disagrees; fix JSON-string input parsing and workflow session state persistence. (#436)
- Filter search results by ACL at the vector query level and fall back to public-only ACL for anonymous users. (#432)
- Improve error reporting for failed A2A agent tasks by including detailed status messages in error outputs. (#426)
- MCP servers on AgentCore Runtime advertise root AS metadata route on 401 to enable direct connect mode. (#427)
- Fix JWT verification for OAuth identity claims to properly validate token signatures. (#409)
- Fix RBAC middleware to issue WARNING level logs on auth failure; pin mcp package to major version 1 to prevent breaking changes. (#413)
- Render A2A image artifacts as MCP ImageContent with inline base64 data and proper MIME types instead of generic blob resources. (#421)
- Fix search API permission issues and optimize response times. (#398)
- Ensure federation sync inherits ACLs for all synced MCP and A2A resources. (#363)
- Fix MCP resource fetching type mismatch; optimize MCP status toggle to single call; increase timeout for status toggle and federation sync operations. (#389)
- Filter A2A agent availability by isEnabled flag instead of active status. (#386)
- Fix federated runtime access synchronization to detect mode drift and preserve user-selected transport during version changes. (#388)
- Fix missing user_id bug; remove unused agentcore_import_service and related federation code. (#382)
- Map inherited ACL roleIds to child resource role catalog during federation sync. (#383)
- Fix A2A workflow path resolution. (#379)
- Serialize WorkflowCanvas before MongoDB update to prevent BSON encoding errors. (#375)
- Store tool input_schema as JSON string to prevent Weaviate from rejecting batches with reserved keys or invalid GraphQL names. (#362)
- Fix card_name metadata to include in vector search results. (#410)
- Remove on_chunk configuration. (#378)

### 🔧 Refactoring & Performance

- Switch vector reranker to AWS Bedrock Cohere and integrate Bedrock session tokens. (#411)
- Remove deprecated status fields from MCP server and A2A agent schemas; migrate A2A enablement to config.enabled with idempotent MongoDB migration. (#403)
- Update Beanie document models to align with asc0.6.0 of Jarvis Chat. (#422)
- Migrate MongoDB transactions from ContextVar-based decorator to explicit session threading through routes and services. (#392)
- Refactor ACL models and consolidate resource type enum across registry and auth-server. (#396)
- Rename agent_invoke.py to agent.py and proxied.py to server.py for improved clarity. (#367)
- Merge A2A agent slug and path into single normalized path field with automatic slash conversion. (#370)
- Extract search routes cleanup and service layer extraction with A2A support. (#358)
- Update A2A polling timeout configuration to ensure consistency with JWT and HTTP timeouts. (#406)
- Sync Beanie model classes with asc0.5.8 tag on Jarvis Chat and apply FlashRank OOM mitigation using semaphore. (#407)

### 📦 Dependencies

- Update SDK embedding to 0.1.6 and remove default spec to always use the last selected model. (#425)
- Bump Jarvis embedding SDK version to fix OpenAI integration issues. (#424)


### Needs Review
- 🔧 Refactoring & Performance Update deployment configuration. (#395) — _Commit message is minimal with no details on what changed; appears to be a one-line fix_
