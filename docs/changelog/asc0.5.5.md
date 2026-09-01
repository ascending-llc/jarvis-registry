---
title: "🚀 Jarvis Registry asc0.5.5"
description: "The asc0.5.5 release of Jarvis Registry"
date: 2026-08-26
tags:
  - Release
---

[← Back to changelog](index.md)

# 🚀 Jarvis Registry asc0.5.5

_August 26, 2026_ · [asc0.5.5 on GitHub](https://github.com/ascending-llc/jarvis-registry/releases/tag/asc0.5.5)

---

### 🐛 Bug Fixes

- Refine token and A2A instruction UI for clarity. (#491)
- Isolate per-resource failures in federation sync's Mongo-apply stage so a single resource failure does not roll back the entire batch. (#494)
- Display redirect_uri on auth-server consent page for improved transparency during OAuth authorization. (#497)
- Relay OAuth client errors through safe redirects with consent gating, redirect validation, login recovery, and replay protection. (#507)
- Safely relay OAuth failures to MCP clients with consent gating, redirect validation, login recovery, and replay protection. (#511)
- Fix device flow error propagation and terminal states. (#519)
- Deduplicate skills by name in list_skills response to prevent duplicates in skill discovery. (#521)
- Raise A2A task budget to 1 hour and implement adaptive timeout handling to prevent long-running agents from timing out during normal execution. (#520)
- Fix stale workflow node run statuses. (#528)

### ✨ Features

- Gate A2A agent execution behind per-agent user consent. (#492)
- Replace loosely typed federationMetadata dictionaries with provider-specific Pydantic models for AWS AgentCore MCP, AWS AgentCore A2A, and Azure AI Foundry A2A. (#490)
- Restrict direct-connect A2A routes to reject DCR clients before agent lookup and prevent unauthorized invocation. (#495)
- Validate non-gRPC transports for A2A agents to ensure only supported transport protocols are used. (#498)
- Add approval gate toolbar and active run polling to provide real-time status updates during workflow execution. (#493)
- Allow authorized users to trigger runs in view-only mode. (#500)
- Serve public managed Agent Card endpoints with Registry proxy URLs, OAuth security metadata, and non-gRPC transport filtering. (#499)
- Add skill models and skills sync API for managing MCP server skill definitions. (#501)
- Add Registry-side OpenTelemetry tracing routed to Langfuse Cloud through OTel Collector with structured operation spans for MCP tool execution and A2A agent execution. (#505)
- Add Skill model fields, RegistryResourceType.SKILL, and skill CRUD endpoints with dual-auth and skill-specific scopes. (#510)
- Add scope ceiling per client category to ensure MCP proxy clients and other DCR clients cannot access unauthorized A2A scopes. (#512)
- Improve OAuth diagnostics and Redis configuration hygiene. (#516)
- Display granted scopes on OAuth consent page to show users what permissions they are granting. (#522)
- Harden workflow run monitoring with adaptive polling, dirty detection, and live node status updates. (#506)
- Display scopes on downstream consent page to provide transparency during OAuth authorization flows. (#524)
- Remove unused auth-server validate endpoint. (#525)
- Extend Registry-side OpenTelemetry tracing with discovery observability, standardized Langfuse trace structure, and W3C trace-context propagation for downstream A2A calls. (#527)

### 🔧 Refactoring & Performance

- Reduce pytest output verbosity for successful test runs and remove deprecation warnings. (#496)
- Allow AI agents to run tests and suppress successful test output to minimize LLM context pollution. (#509)
- Remove dead code from fork source including the ./cli/ folder to eliminate confusion with jarvis-registry-cli release artifacts. (#523)
