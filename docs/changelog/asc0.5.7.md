---
title: "🚀 Jarvis Registry asc0.5.7"
description: "The asc0.5.7 release of Jarvis Registry"
date: 2026-09-01
tags:
  - Release
---

[← Back to changelog](index.md)

# 🚀 Jarvis Registry asc0.5.7

_September 01, 2026_ · [asc0.5.7 on GitHub](https://github.com/ascending-llc/jarvis-registry/releases/tag/asc0.5.7)

---

### ✨ Features

- Add OAuth preflight check and re-authorization flow for workflow runs, enabling workflows to refresh expired MCP server credentials before execution. (#534)
- Add standalone scheduled workflow worker with lease-based locking, workflow management APIs, and container deployment integration for running scheduled workflows at scale. (#540)
- Include deployment environment in Registry telemetry to enable observability tools like Langfuse to display the correct environment context. (#544)
- Propagate Registry deployment environment to downstream A2A agents via baggage, ensuring Langfuse displays the correct environment for agent spans. (#545)
- Propagate Langfuse trace name and tags to A2A agent baggage, enabling downstream agent and LLM spans to participate in the same distributed trace. (#547)

### 🐛 Bug Fixes

- Refine Registry frontend detail actions, categories, sharing, and deletion flows; improve layout and UI overflow handling in skills list and file preview. (#536)
- Replace per-piece workflow prompt truncation with a single aggregate cap (default 3.6M chars) to prevent context window overflow with Sonnet-class LLMs and unskip related tests. (#541)

### 🔧 Refactoring & Performance

- Consolidate encryption primitives and Azure Foundry authentication into shared registry-pkgs; enable scheduled workflow worker to authenticate Azure Foundry agents. (#542)
- Add bounded concurrency to federation and skill source sync operations, and improve robustness by preserving resources on discovery failures. (#543)
- Move shared OAuth services and auth-field encryption to registry-pkgs; enable scheduled workflow worker to authenticate and call OAuth-protected MCP servers. (#546)
