---
title: "🚀 Jarvis Registry asc0.5.21"
description: "The asc0.5.21 release of Jarvis Registry"
date: 2026-11-25
tags:
  - Release
---

[← Back to changelog](index.md)

# 🚀 Jarvis Registry asc0.5.21

_November 25, 2026_ · [asc0.5.21 on GitHub](https://github.com/ascending-llc/jarvis-registry/releases/tag/asc0.5.21)

---

### ✨ Features

- Adds an API to list a user's app consents. (#696)
- Indexes MCP resource templates found during server discovery and returns them in discovery results. (#698)
- Unifies how the gateway runs downstream MCP operations through a shared authorization and execution path. (#699)
- Adds gateway support for reading MCP resources and retrieving prompts from downstream servers, preserving native result content. (#700)
- Adds APIs to revoke app and resource consents. (#701)
- Adds scan results, security views, scan and preview labels in the file tree, and model configuration to the Skills UI. (#694)

### ⚠️ Breaking Changes & Upgrade Notes

- Requires configured models for workflow LLM steps and seeds a default workflow model from legacy settings. (#702)

### 🐛 Bug Fixes

- Refuses tools/call requests for tools disabled in the registry on the direct proxy, so disabled tools are no longer executed downstream. (#688)
- Caps the Weaviate rerank result count to the number of candidates and logs sanitized downstream call failures. (#703)
