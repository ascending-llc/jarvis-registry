---
title: "🚀 Jarvis Registry asc0.5.4"
description: "The asc0.5.4 release of Jarvis Registry"
date: 2026-07-29
tags:
  - Release
---

[← Back to changelog](index.md)

# 🚀 Jarvis Registry asc0.5.4

_July 29, 2026_ · [asc0.5.4 on GitHub](https://github.com/ascending-llc/jarvis-registry/releases/tag/asc0.5.4)

---

### ✨ Features

- Exposes workflow trigger parameters (initial_input) to all workflow node prompts, allowing downstream nodes to access values supplied at invocation time. (#479)
- Enforces executor access control (VIEW permission) when creating or updating workflows to prevent unauthorized execution. (#480)
- Supports creating headless agent tokens without requiring server consent, with token purpose selection in the UI. (#481)
- Adds Agent-to-Agent proxy connection instructions to agent cards for direct peer-to-peer communication setup. (#485)
- Displays federation partial success status and per-runtime vector sync failure information on the federation registry UI, including unimported item counts. (#486)
- Implements federation partial success tracking and per-runtime vector sync failure reporting, with detailed semantics for conflict resolution and import outcomes. (#487)

### 🐛 Bug Fixes

- Catches executor exceptions in wrapper to properly attribute errors to individual workflow nodes instead of failing the entire workflow. (#482)
- Finalizes orphaned workflow node runs when a workflow fails or is canceled to ensure consistent state. (#483)
- Corrects node error display in workflow run history to accurately reflect per-node error information. (#484)

### 🔧 Refactoring & Performance

- Migrates Auth, RBAC, and CSRF middleware from BaseHTTPMiddleware to pure ASGI middleware for improved cancellation scope handling and concurrency safety. (#488)
