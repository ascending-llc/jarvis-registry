---
title: "🚀 Jarvis Registry asc0.5.12"
description: "The asc0.5.12 release of Jarvis Registry"
date: 2026-10-05
tags:
  - Release
---

[← Back to changelog](index.md)

# 🚀 Jarvis Registry asc0.5.12

_October 05, 2026_ · [asc0.5.12 on GitHub](https://github.com/ascending-llc/jarvis-registry/releases/tag/asc0.5.12)

---

### ✨ Features

- This release introduces a built-in MongoDB migration runner that replaces manual deployment scripts, an automated embedding-reindex pipeline for vector search, and native workflow scheduling in the UI. It also improves skill visibility with new source indicators and adds an API endpoint to track reindex job history. Together these changes reduce manual deployment steps and give platform teams better insight into background maintenance tasks. (#597)
- Adds an automated embedding-reindex pipeline that rebuilds vector search indexes in the background, temporarily pausing only the affected writes and searches until the new index is ready. (#597)
- Adds a source indicator column to the Skills list showing whether each skill is locally defined or provided by an external server. (#600)
- Adds a read-only API endpoint for listing embedding reindex jobs along with their status and run history. (#601)
- Adds workflow scheduling support, letting users configure recurring run schedules directly from the workflow editor. (#602)
- Adds a built-in MongoDB data-migration runner that applies versioned migrations automatically during deployment, replacing manual one-off scripts and reducing the risk of missed backfills. (#604)

### 🔧 Refactoring & Performance

- Updates the pre-commit and post-merge git hook configuration used by the project's development tooling. (#599)
