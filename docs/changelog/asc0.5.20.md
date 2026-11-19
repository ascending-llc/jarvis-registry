---
title: "🚀 Jarvis Registry asc0.5.20"
description: "The asc0.5.20 release of Jarvis Registry"
date: 2026-11-19
tags:
  - Release
---

[← Back to changelog](index.md)

# 🚀 Jarvis Registry asc0.5.20

_November 19, 2026_ · [asc0.5.20 on GitHub](https://github.com/ascending-llc/jarvis-registry/releases/tag/asc0.5.20)

---

### ⚠️ Breaking Changes & Upgrade Notes

- Enables the 2026-07-28 MCP protocol version by default (`MCPGW_MODERN_PROTOCOL_ENABLED` now defaults to true), so upgraded environments support both protocol versions without extra configuration; set the variable to false to roll back. Upgrade note: remove any existing `MCPGW_MODERN_PROTOCOL_ENABLED=false` entry from client secrets, or the modern protocol stays off. (#690)

### 🔧 Refactoring & Performance

- Removes the Anthropic registry import and federation design documents and their related code and UI paths, and refreshes the LLM-facing docs. (#686)
- Reworks the OAuth consent and token-grant flow in the auth server, including changes to how consent and OAuth state are stored. (#685)

### 🌍 Documentation

- Moves the private skill registry section above Quick Start in the README and clarifies its coding-team use case, package audiences, client delivery, and EKS/AKS deployment route. (#691)
