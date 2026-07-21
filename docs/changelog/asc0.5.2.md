---
title: "🚀 Jarvis Registry asc0.5.2"
description: "The asc0.5.2 release of Jarvis Registry"
date: 2026-07-10
tags:
  - Release
---

[← Back to changelog](index.md)

# 🚀 Jarvis Registry asc0.5.2

_July 10, 2026_ · [asc0.5.2 on GitHub](https://github.com/ascending-llc/jarvis-registry/releases/tag/asc0.5.2)

---

### ✨ Features

- Wire up AWS Bedrock AIP for workflow runners, validate LLM and AIP environment variables at startup, and consolidate A2A agent configuration by removing the separate agents folder. (#451)
- Add explicit server-level consent checks before clients invoke downstream MCP servers through the gateway, with approval APIs and frontend consent screens. (#452)
- Add step_objective field to workflow steps to enable step-level prompt rendering with intention data and context injection. (#450)
- Require cached per-user OAuth client consent before issuing registry-managed tokens, with Redis-backed consent state and frontend consent flows with deep-link support. (#444)
- Implement refresh token rotation with 14-day session caps, filter unknown IdP groups before token issuance, harden refresh token validation, and add session start claims. (#445)

### 🐛 Bug Fixes

- Refactor OpenTelemetry metrics collection to fix server ID generation, remove unused metrics, and add explicit error type tracking. (#440)

### 🔧 Refactoring & Performance

- Update the release changelog generation action to handle existing branches and closed PRs gracefully. (#449)
