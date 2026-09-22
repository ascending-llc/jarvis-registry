---
title: "🚀 Jarvis Registry asc0.5.9"
description: "The asc0.5.9 release of Jarvis Registry"
date: 2026-09-22
tags:
  - Release
---

[← Back to changelog](index.md)

# 🚀 Jarvis Registry asc0.5.9

_September 22, 2026_ · [asc0.5.9 on GitHub](https://github.com/ascending-llc/jarvis-registry/releases/tag/asc0.5.9)

---

### ✨ Features

- Adds distributed tracing propagation so trace context follows requests through downstream MCP tool executions and A2A agent calls, improving cross-service observability for proxied workflows (#575)
- Adds a normalized server name field to MCP server records and backfills existing data so server matching, deduplication, and federation sync stay consistent with the shared uniqueness rules used elsewhere in the platform (#577)

### 🐛 Bug Fixes

- Resolves an issue where the Step Objective panel's resize handle now matches the current theme color instead of a mismatched fixed color (#574)
