---
title: "🚀 Jarvis Registry asc0.5.8"
description: "The asc0.5.8 release of Jarvis Registry"
date: 2026-09-14
tags:
  - Release
---

[← Back to changelog](index.md)

# 🚀 Jarvis Registry asc0.5.8

_September 14, 2026_ · [asc0.5.8 on GitHub](https://github.com/ascending-llc/jarvis-registry/releases/tag/asc0.5.8)

---

### ✨ Features

- Adds support for base64-encoded supporting files in skill create/update/read operations and raises the nginx API proxy body-size limit so larger uploads no longer return 413 errors (#561)
- Adds Google Workspace SSO login with group-based role sync alongside the existing Entra integration (#556)
- Adds validated, lossless round-tripping of skill frontmatter between backend and frontend, prevents internal registry fields from leaking into skill frontmatter, and renders GitHub-style markdown tables in the skill editor (#552)
- Adds a resizable Markdown editor modal for editing workflow step objectives (#564)
- Preserves the original formatting of step objectives when they are rendered into workflow prompts (#569)
- Adds protection against deleting Azure Foundry agents that appear stale due to transient sync issues rather than actual removal (#555)

### 🐛 Bug Fixes

- Treats the Claude Code CLI as supporting URL-mode elicitation even though it misreports this capability, improving the login/consent experience for Claude Code users (#572)
- Resolves an issue in the MCP registry server list endpoint where servers with a missing path or description could produce broken proxy URLs or crash the response (#568)
- Ensures that renaming a skill's display title also updates its underlying identifier and file path, instead of only updating the display text (#571)
- Hardens login against identity-provider group-resolution failures for Google and Entra so authentication no longer fails ungracefully when group lookups error out (#565)
- Stops rendering null-valued skill frontmatter fields and ensures the name field always appears first followed by description (#562)
- Warns users before triggering a workflow if they have unsaved changes, preventing accidental loss of edits (#558)
- Enforces that OAuth clients only use grant types they declared during dynamic client registration, closing a gap that allowed undeclared grant types to be used (#557)
- Enforces that skill frontmatter fences must stand alone on their own line during skill sync, rejecting malformed frontmatter that previously could slip through (#559)

### 🔧 Refactoring & Performance

- Removes the unused Anthropic registry API scaffold and its associated documentation and tests (#566)
- Simplifies the GitHub OAuth callback for skill sync to a single fixed URL resolved from OAuth state, removing the need for per-source app registration (#560)
- Replaces order-dependent stubbed test doubles in AgentCore federation sync tests with order-independent fakes to eliminate CI flakiness from concurrent calls (#570)
- Adds telemetry and structured logging startup for the workflow worker service and fixes a scheduler crash caused by timezone-naive timestamps read from MongoDB (#551)
- Adds a dedicated deployment step for the workflow worker service and simplifies the overall deployment workflow configuration (#553)
