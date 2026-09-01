---
title: "🚀 Jarvis Registry asc0.5.6"
description: "The asc0.5.6 release of Jarvis Registry"
date: 2026-08-26
tags:
  - Release
---

[← Back to changelog](index.md)

# 🚀 Jarvis Registry asc0.5.6

_August 26, 2026_ · [asc0.5.6 on GitHub](https://github.com/ascending-llc/jarvis-registry/releases/tag/asc0.5.6)

---

### 🐛 Bug Fixes

- Update tag-related workflows to ignore CLI repository tags and process only registry release tags (asc* prefix). (#531)
- Fix workflow node dependency resolution in DAG helpers by restricting implicit parent lookup to single hop, enabling nodes within branching structures to correctly reference upstream outputs. (#537)

### ✨ Features

- Add end-to-end OpenTelemetry observability for workflow execution with metrics for CRUD operations, workflow runs, per-step agent invocations, distributed tracing via AgnoInstrumentor and Tempo, and structured JSON logging with trace context correlation. (#518)
- Add skill sync source management with models, CRUD service with ACL access control, pagination, full-text search, and OAuth routes for syncing skills from GitHub repositories. (#526)
- Add run statistics to workflow list endpoint, including execution counts and performance metrics for each workflow. (#532)
- Complete GitHub-backed skill sync execution with tarball download, Markdown discovery, MongoDB persistence, OAuth callback-triggered sync, and ACL inheritance from sources. (#530)
- Add scheduled workflow management with lease-based locking for worker coordination, WorkflowSchedule model with lease tokens, schedule routes, and RBAC authorization. (#533)
- Implement Langfuse tracing for workflow executions and continuations, grouping workflow root spans with Agno child spans and recording input/output, execution status, and user/session metadata. (#535)
