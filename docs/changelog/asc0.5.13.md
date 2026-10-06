---
title: "🚀 Jarvis Registry asc0.5.13"
description: "The asc0.5.13 release of Jarvis Registry"
date: 2026-10-06
tags:
  - Release
---

[← Back to changelog](index.md)

# 🚀 Jarvis Registry asc0.5.13

_October 06, 2026_ · [asc0.5.13 on GitHub](https://github.com/ascending-llc/jarvis-registry/releases/tag/asc0.5.13)

---

### ✨ Features

- Adds a GitHub Federation skill source UX, including a confirmation dialog before redirecting to GitHub authorization, a default repository path of the whole repo instead of `skills/`, an updated favicon, and simplifies the skill editor by removing the "Always Apply" toggle. (#598)
- Adds syntax highlighting and clickable internal file links to the Skills content editor and viewer, making it easier to navigate and read skill markdown files. (#607)

### ⚠️ Breaking Changes & Upgrade Notes

- Removes the unused Cognito and Keycloak auth providers; `AUTH_PROVIDER` now only accepts `entra` or `google`, and requests to removed provider login routes return 422. (#606)

### 🐛 Bug Fixes

- Fixes Entra group sync so it actually runs when `AUTH_PROVIDER` is not `entra` but `ENTRA_GROUP_SYNC_ENABLED` is true, instead of silently using a do-nothing client. (#606)
- Removes the unused `/config` auth-server route, which previously failed on every request, and narrows the agent, server and workflow create/delete error handling so unexpected failures return a 500 instead of leaking internal error text as a 400. (#608)
- Resolves an issue where every workflow run failed at its first MCP step because the Bedrock fallback model rejected unsupported `temperature`/`top_p` sampling parameters; workflow runs using the Claude Sonnet 5 fallback model now succeed. (#609)

### 🔧 Refactoring & Performance

- Removes dead auth-provider code paths and the `python-jose`, `factory-boy`, and `faker` dependencies, and rewrites the agent and development documentation to match current codebase behavior. (#608)
