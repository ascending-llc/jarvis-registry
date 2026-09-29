---
title: "🚀 Jarvis Registry asc0.5.10"
description: "The asc0.5.10 release of Jarvis Registry"
date: 2026-09-22
tags:
  - Release
---

[← Back to changelog](index.md)

# 🚀 Jarvis Registry asc0.5.10

_September 22, 2026_ · [asc0.5.10 on GitHub](https://github.com/ascending-llc/jarvis-registry/releases/tag/asc0.5.10)

---

### ✨ Features

- Adds a Model Source system with new access scopes and CRUD API endpoints, letting administrators register and manage model configurations used by workflow model selection. (#576)
- Adds default workflow model selection backed by registered model sources, and switches workflow execution to use native Bedrock and Azure OpenAI model integrations. (#580)
- Extends the Model Source API so the vector search backend can resolve its embedding model from a registered model source instead of static configuration. (#581)
- Adds a dry-run "test connection" option for skill sync and stops OAuth callbacks from automatically triggering a sync. (#582)
- Adds per-server tool enable/disable controls that are enforced in discovery search and tool execution, plus a backfill script to apply the new setting to existing MCP servers. (#584)
- Adds a maintenance gate that blocks writes and searches during a Weaviate embedding reindex job, returning a clear 503 response instead of inconsistent results. (#585)

### ⚠️ Breaking Changes & Upgrade Notes

- Removes the security scanner feature for agents and MCP servers, along with its unused configuration, dependencies, and related UI elements. (#578)

### 🐛 Bug Fixes

- Resolves an issue where workflows could continue running or resume after a terminal step failure or a persistence error, ensuring failed workflows now halt correctly. (#583)
- Resolves an issue where the access-role seeding script only covered Chat, so that MCP server and remote agent access roles are now seeded as well. (#586)

### 🔧 Refactoring & Performance

- Updates the release-notes generation step in the release workflow to authenticate with a dedicated Copilot credential. (#587)
