---
title: "🚀 Jarvis Registry asc0.5.15"
description: "The asc0.5.15 release of Jarvis Registry"
date: 2026-10-28
tags:
  - Release
---

[← Back to changelog](index.md)

# 🚀 Jarvis Registry asc0.5.15

_October 28, 2026_ · [asc0.5.15 on GitHub](https://github.com/ascending-llc/jarvis-registry/releases/tag/asc0.5.15)

---

### 🔧 Refactoring & Performance

- Moves embedding reindex and skill sync onto a shared lease mechanism so only one instance runs each job at a time, and fixes a case where a failed source write after job completion left a skill source stuck in a syncing state. (#617)
- Pins pre-commit Python hook environments to Python 3.12 so hook installation works with newer system Pythons. (#631)
- Resolves intermittent failure in the ACL role reload throttle test by making its clock deterministic. (#641)
- Switches pre-commit secret scanning from tartufo to gelato, fixes lock-file exclusion patterns for every workspace, and removes the Python version pin and unused CI block from the pre-commit config. (#646)
- Adds status-guarded workflow run writes that handle concurrent changes safely, and clears pending requirements when a run reaches a terminal state. (#618)

### ✨ Features

- Adds a Models page for creating, editing and deleting model sources, and lets the gateway select a model. (#619)
- Binds Entra scope groups to object IDs, validates Google group domains, and validates configured Entra scope-group bindings at startup on every service that uses them. (#620)
- Adds token-fenced run leases, expired-run recovery and bounded graceful shutdown for workflow runs, including scheduled runs, so interrupted runs are recovered and their schedule claims released. (#623)
- Adds identity backfill and safe user cleanup scripts for resolving user identity conflicts, with a documented cleanup workflow. (#625)
- Resolves and provisions users by verified OIDC identity, uses stable user IDs in JWTs, corrects discovery metadata, logs untrusted-email login refusals, and shows the refusal reason on the device sign-in page. (#628)
- Adds run leases and expired-run recovery for workflow runs on top of guarded run-state writes. (#633)
- Adds a Claude Desktop static OAuth client and logs the reason when an authorization code is rejected during token exchange. (#642)
- Warns at workflow save time when a per-node modelSourceId is set on a node that calls an A2A agent directly, where the setting is ignored, and documents per-node model source selection. (#639)
- Adds leader-leased vector reconciliation and orphan sweeping, makes the MCP gateway stateless with a shared client-info cache, moves OAuth flow state to Redis, and removes the old MCP connection service. (#636)

### 🌍 Documentation

- Improves search engine metadata for the Jarvis Registry gateway site. (#622)

### 🐛 Bug Fixes

- Builds the registry MCP URL from the runtime origin and base path so the displayed URL is correct under any deployment path. (#630)
- Rebuilds vectors when the stored metadata document count differs from the expected count, so incomplete entity vectors are repaired. (#634)
- Resolves issue where the Atlassian MCP server failed on initialize: the direct MCP proxy now returns proper JSON-RPC errors for non-tool methods, and health checks flag likely transport/URL mismatches. (#640)
- Stops the MCP gateway from dispatching a request when the client disconnects before the request body is complete. (#644)
