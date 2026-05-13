---
title: "🚀 Jarvis Registry asc0.2.12"
description: "JWT security hardening, AgentCore federation auth, workflow refactor, and documentation expansion"
date: 2025-05-13
tags:
  - Release
---

[← Back to changelog](index.md)

# 🚀 Jarvis Registry asc0.2.12

_May 13, 2025_  · [asc0.2.12 on GitHub](https://github.com/ascending-llc/jarvis-registry/releases/tag/asc0.2.12)

---

This release focuses on security hardening around JWT generation, AgentCore federation authentication with Redis caching, a workflow execution refactor, and a major expansion of the Explore Agentic documentation section.

## ⚠️ Breaking Changes & Upgrade Notes

- MongoDB schema upgraded to asc0.5.3 — run the migration before deploying. Lingering legacy attributes have been removed from the schema. (#322)
- JWT generation has moved from the auth-server to the registry. The previously unauthenticated auth-server endpoint has been removed. Update any clients calling that endpoint directly. (#312)

## ✨ Features

- **AgentCore federation auth** — JWT authentication for AgentCore Runtime federated MCP servers, with Redis caching to reduce token overhead on repeated requests. (#316)
- **Federation access roles** — Federation access roles are now seeded automatically during deployment, removing the need for manual setup. (#321)
- **Workflow execution refactor** — Workflow execution and models restructured into a cleaner, more maintainable layout. (#315)

## 🔒 Security

- JWT generation moved entirely to the registry; the unauthenticated auth-server endpoint has been removed to close an unprotected access vector. (#312)

## 🐛 Bug Fixes

- Preserve active dashboard tab when navigating back; improve cursor styles throughout the UI. (#317)
- Align RoleDropdown buttons for consistent layout. (#320)

## 🌍 Documentation

- Add full Explore Agentic content section covering Agentic AI, MCP, AI Governance, Enterprise RAG, and the Agentic Glossary. (#332)
- Add Mermaid diagram CSS fix and SEO improvements for guidelines pages. (#335)
- Additional doc updates and content improvements. (#324, #331)

---

**Full Changelog**: https://github.com/ascending-llc/jarvis-registry/commits/asc0.2.12
