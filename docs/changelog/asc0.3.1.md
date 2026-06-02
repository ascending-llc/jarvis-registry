---
title: "🚀 Jarvis Registry asc0.3.1"
description: "✨ Features"
date: 2026-05-26
tags:
  - Release
---

[← Back to changelog](index.md)

# 🚀 Jarvis Registry asc0.3.1

_May 26, 2026_ · [asc0.3.1 on GitHub](https://github.com/ascending-llc/jarvis-registry/releases/tag/asc0.3.1)

---

### ✨ Features

- Adds a workflow execution engine with MongoDB-backed run state, support for dispatching steps to A2A agents and MCP servers, and runtime control APIs for pause, resume, cancel, and retry operations. (#329)
- Adds REST endpoints for creating, updating, deleting, listing, and triggering persisted workflow definitions and their execution runs. (#333)
- Adds access token scope negotiation to prevent privilege escalation and implements refresh token rotation per OAuth 2.1. (#346)
- Adds A2A agent discovery to the `/search/servers` endpoint and the gateway's `discover_servers` tool, and fixes filter-key mismatches that caused disabled A2A agents to always appear in search results. (#339)
- Adds an `execute_agent` MCP tool to the gateway that invokes registered A2A agents on behalf of the caller. (#349)
- Adds direct-connect proxy routes at `/proxy/a2a/{slug}` supporting both JSON-RPC (`POST`) and HTTP+JSON (`GET`/`POST /{path}`) bindings, allowing clients to reach A2A agents through the registry without additional configuration. (#350)
- Renames the MCP server capabilities refresh action from 'health' to 'capabilities' and adds a new `POST /agents/{id}/refresh` endpoint for refreshing A2A agent capabilities. (#354)
- Adds support for multi-step `CONDITION` branches via `true_steps`/`false_steps` fields and `ROUTER` choices via a `choices` field in workflow definitions. (#355)

### 🔧 Refactoring & Performance

- Simplifies Weaviate vector synchronization for A2A agents and MCP servers by introducing a shared base repository and content-hash-based change detection, eliminating redundant re-embedding on unchanged content. (#336)
- Adds a CI pipeline that automatically indexes the codebase with GitNexus on every push to main, enabling code-intelligence features such as impact analysis and symbol navigation. (#341)
- Adds automated release changelog generation and Docker image tagging CI workflows that trigger on version tags. (#344)

### 🐛 Bug Fixes

- Resolves several auth-server compliance gaps: DCR responses now correctly advertise supported grant types and PKCE methods, the token endpoint accepts both JSON and form-encoded bodies, and the `/.well-known/oauth-protected-resource` path is corrected — collectively making the auth-server compatible with Claude Code CLI without requiring explicit JWTs. (#338)
- Fixes low-contrast appearance and unreadable disabled state for the RadioGroupField component in the frontend. (#340)
- Fixes workflow executors and utility scripts that were querying on `status == 'active'` to instead use the correct `config.enabled` field for MCP servers and `isEnabled` for A2A agents, so enabled/disabled filtering works as intended. (#351)
- Renames the gateway's MCP discovery tool from `discover_mcp_entities` to `discover_servers`. (#359)

### ⚠️ Breaking Changes & Upgrade Notes

- Removes the `A2AAgent.federationMetadata.sourceType` field entirely; use `providerType` instead to determine whether an agent is hosted on AgentCore Runtime. (#357)
