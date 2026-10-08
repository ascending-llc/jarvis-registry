---
title: "🚀 Jarvis Registry asc0.5.14"
description: "The asc0.5.14 release of Jarvis Registry"
date: 2026-10-08
tags:
  - Release
---

[← Back to changelog](index.md)

# 🚀 Jarvis Registry asc0.5.14

_October 08, 2026_ · [asc0.5.14 on GitHub](https://github.com/ascending-llc/jarvis-registry/releases/tag/asc0.5.14)

---

### 🐛 Bug Fixes

- Fixes 401 `invalid_token` errors on MCP servers like Atlassian by discovering OAuth metadata at every interactive login, giving Registry its own OAuth client and tokens separate from Jarvis Chat, and refreshing expired tokens once before re-prompting login. (#615)
- Enabling or refreshing a server whose OAuth token expired now returns an `oauth_required` error and opens the authorization modal, so the Authorize button no longer hangs on a stale flow. (#614)
- Respects Claude Desktop's declared MCP client capabilities so it no longer hangs on URL-mode elicitation responses. (#613)
- Generated non-interactive agent tokens now carry scopes derived from the user's group mappings instead of being empty, scopes beyond the token type's limit return a clear 400, and managed-agent tokens with an empty scope are rejected with a 401. (#611)

### 🔧 Refactoring & Performance

- Updates migration init container images together with the main container in internal deploys, so each rollout runs the new release's migrations. (#612)
