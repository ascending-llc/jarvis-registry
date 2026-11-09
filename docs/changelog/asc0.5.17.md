---
title: "🚀 Jarvis Registry asc0.5.17"
description: "The asc0.5.17 release of Jarvis Registry"
date: 2026-11-02
tags:
  - Release
---

[← Back to changelog](index.md)

# 🚀 Jarvis Registry asc0.5.17

_November 02, 2026_ · [asc0.5.17 on GitHub](https://github.com/ascending-llc/jarvis-registry/releases/tag/asc0.5.17)

---

### 🐛 Bug Fixes

- Fixes Google Workspace group access grants not reaching users first created by Chat; Google users are now matched to groups by email, while Entra users still match by their directory object ID. (#654)
- Preserves existing embeddings when updating server and agent metadata such as enabled state, tags and runtime version, which previously failed with a 500 error in environments without static AWS keys or silently overwrote the stored vector. (#653)
- Scopes Google Cloud Identity group lookups to the Workspace customer through a new optional `GOOGLE_CUSTOMER_ID` setting, so logins and group sync no longer fail for users who belong to groups owned by other organizations, and improves logging of Google error details. (#656)
- Uses URL-mode elicitation only for clients on protocol version 2025-11-25 or later, and includes the consent URL in the model-facing messages so clients such as Codex CLI can show users the consent link. (#657)
- Fetches a newly registered MCP server's capabilities in the background, so server creation returns immediately instead of timing out and reporting a committed server as failed. (#655)

### 🌍 Documentation

- Reorganizes the documentation, adding a production configuration guide and ChatGPT/Claude custom connector screenshots, and removing the outdated auth and configuration pages. (#658)
- Updates the production configuration guide. (#659)
- Adds a script that generates LLM-readable documentation files (`llms.txt` and `llms-full.txt`) from the docs. (#660)
- Updates the production configuration guide. (#661)
