---
title: "🚀 Jarvis Registry asc0.5.11"
description: "The asc0.5.11 release of Jarvis Registry"
date: 2026-09-24
tags:
  - Release
---

[← Back to changelog](index.md)

# 🚀 Jarvis Registry asc0.5.11

_September 24, 2026_ · [asc0.5.11 on GitHub](https://github.com/ascending-llc/jarvis-registry/releases/tag/asc0.5.11)

---

### ✨ Features

- Adds GitHub-based skill sync so servers can pull skills from GitHub repositories with enforced read-only permissions, and introduces per-server controls to enable or disable individual tools. (#592)

### 🐛 Bug Fixes

- Resolves an issue where the GitHub skill sync OAuth login redirected to an insecure http:// URL behind a TLS-terminating proxy by building the redirect URI from the configured registry URL instead of the incoming request. (#593)
- Resolves several GitHub skill sync issues: skills whose name no longer matches their folder are now rejected during discovery instead of silently accepted, and the Last Sync status card now shows the correct failed skill and error message instead of "Unknown skill". (#594)
