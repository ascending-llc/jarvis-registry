---
title: "🚀 Jarvis Registry asc0.5.16"
description: "The asc0.5.16 release of Jarvis Registry"
date: 2026-10-29
tags:
  - Release
---

[← Back to changelog](index.md)

# 🚀 Jarvis Registry asc0.5.16

_October 29, 2026_ · [asc0.5.16 on GitHub](https://github.com/ascending-llc/jarvis-registry/releases/tag/asc0.5.16)

---

### ✨ Features

- Adds a migration so API key fields saved on MCP servers are stored in a consistent format, returned in the expected format on read, and default correctly when switching a server from OAuth to API Key authentication. (#643)
- Improves discovery guidance in the Registry MCP gateway: empty discovery results now return hints tailored to the search that ran, and the MCP instructions and client documentation are simplified. (#638)

### 🐛 Bug Fixes

- Preserves a server's stored initialization requirement when a connection probe fails or its session ID can't be read, so stateful servers are no longer saved as not requiring initialization; includes a migration to clear incorrectly saved values. (#649)

### 🌍 Documentation

- Clarifies MCP connection setup, permission boundaries, and private skill delivery across ten documentation pages, with copyable MCP configuration and repaired links. (#647)
