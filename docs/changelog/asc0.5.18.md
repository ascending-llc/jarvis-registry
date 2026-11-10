---
title: "🚀 Jarvis Registry asc0.5.18"
description: "The asc0.5.18 release of Jarvis Registry"
date: 2026-11-10
tags:
  - Release
---

[← Back to changelog](index.md)

# 🚀 Jarvis Registry asc0.5.18

_November 10, 2026_ · [asc0.5.18 on GitHub](https://github.com/ascending-llc/jarvis-registry/releases/tag/asc0.5.18)

---

### ✨ Features

- Adds OpenAI and Anthropic as model source providers, with a configurable base URL so compatible third-party vendors can be used. (#645)
- Adds skill scan models, job queueing and enqueue helpers as groundwork for automated skill security scanning. (#667)
- Adds a Codex tab with TOML-based MCP configuration support. (#668)
- Adds column sorting and multi-field search, with sort indicators that persist. (#666)
- Adds automated skill scanning: scans are queued when skills are written, with RBAC-protected endpoints to read scan results and trigger manual scans, and an admin-selectable scan model. (#670)
- Continues the skill scan rollout with further worker and scanner updates. (#674)
- Continues the skill scan rollout with further worker and scanner updates. (#675)

### 🐛 Bug Fixes

- Improves skill scan worker reliability so scans no longer hang or leave stray processes when a scanner tool exits. (#673)
- Prevents a GitHub authorization from being replayed after a successful callback. (#672)

### 🔧 Refactoring & Performance

- Removes the deprecated health monitoring feature and the unused websockets dependency from the registry. (#664)

### 🌍 Documentation

- Adds an API reference for the skill scan endpoints for frontend developers. (#671)
- Rewrites the MCP access and private skill walkthrough introductions and adds a guide comparing a Git repository with a private agent skill registry. (#665)
