---
title: "🚀 Jarvis Registry asc0.5.19"
description: "The asc0.5.19 release of Jarvis Registry"
date: 2026-11-18
tags:
  - Release
---

[← Back to changelog](index.md)

# 🚀 Jarvis Registry asc0.5.19

_November 18, 2026_ · [asc0.5.19 on GitHub](https://github.com/ascending-llc/jarvis-registry/releases/tag/asc0.5.19)

---

### ✨ Features

- Adds URL elicitation support for MCP servers reached through the gateway's direct proxy URL. (#683)
- Adds per-node model selection to the workflow canvas and preserves per-node model overrides when a workflow is saved. (#679)

### 📦 Dependencies

- Upgrades the agno (3.0.5) and mcp (2.3) dependencies, and moves the workflow step error policy, run persistence and MCP client and gateway to match. (#677)
- Continues the agno 3 and MCP SDK 2.3 upgrade by hardening the MCP client status check: it bounds the handshake and no longer records redirect responses as the server's init state. (#678)
- Continues the agno 3 and MCP SDK 2.3 upgrade with a fix so gateway URL elicitation uses one input key per check. (#681)
- Completes the agno 3 and MCP SDK 2.3 upgrade series, including the final URL elicitation changes in the gateway. (#684)

### 🌍 Documentation

- Clarifies the Quick Start and Docker Compose setup docs, adds Google Workspace setup instructions, and removes unused environment variables from the Compose file and .env.example. (#680)
