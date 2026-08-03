---
title: "🚀 Jarvis Registry asc0.5.3"
description: "The asc0.5.3 release of Jarvis Registry"
date: 2026-07-24
tags:
  - Release
---

[← Back to changelog](index.md)

# 🚀 Jarvis Registry asc0.5.3

_July 24, 2026_ · [asc0.5.3 on GitHub](https://github.com/ascending-llc/jarvis-registry/releases/tag/asc0.5.3)

---

### ✨ Features

- Add Microsoft Azure AI Foundry as a federation provider to discover, register, and invoke Foundry-hosted agents using Microsoft Entra ID authentication. (#377)
- Add support for referencing upstream node outputs and implementing dynamic upstream references for workflow step nodes. (#455)
- Enhance workflow StepOutput to parse all A2A media types (images, videos, audio, files) and automatically inject direct parent node outputs as dependencies. (#456)
- Preserve the interrupted SPA route through OAuth login and safely redirect users back to their original page after authentication. (#457)
- Redirect authenticated users to their originally-requested protected page instead of the default landing page. (#458)
- Add workflow share functionality with permission-based access control for collaborative workflow management. (#459)
- Implement real OAuth device authorization flow with user consent mechanism for MCP clients. (#462)
- Add Azure AI Foundry badges to synced agent and MCP server cards for visual identification. (#469)

### 🔧 Refactoring & Performance

- Derive AgentCore runtime JWT claims from resource configuration to improve federated service authentication. (#453)
- Apply mechanical refactoring and update test assertions to improve code maintainability. (#460)
- Update input form boxes for Azure Federation configuration to match actual backend requirements. (#461)
- Hide test endpoint icon for federated agents and servers in the UI. (#463)
- Unify A2A client authentication and strip content-encoding headers in proxy responses to prevent decompression issues. (#471)
- Run federation synchronization asynchronously with job polling for improved responsiveness. (#473)
- Extract shared AgentCore JWT signing into registry-pkgs for reuse across services and cache runtime JWTs in Redis with configurable expiry. (#474)

### 🐛 Bug Fixes

- Fix elicitation completion notification to only send when MCP client declares support for URL mode elicitation. (#454)
- Fix AgentCore federation inbound auth configuration change detection for JWT authentication with updated allowed audiences. (#464)
- Fix Azure Foundry federated agents to handle A2A transport without required artifact IDs, promote data-URI media into structured formats, and harden protocol discovery. (#465)
- Prevent vector database synchronization from blocking when a single resource enrichment operation fails. (#470)
- Fix Azure Foundry icon display on agent and server cards. (#472)
- Connect workflow MCP executors directly to downstream servers instead of through the registry proxy, enforcing ACL and consent checks at the executor boundary. (#475)
- Fix Azure Foundry agent card refresh authentication and API path routing. (#476)
