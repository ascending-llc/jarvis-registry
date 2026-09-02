---
name: load-jarvis-context
description: Inject shared background on how Jarvis Chat, Jarvis Registry, and Jarvis Registry CLI interconnect (product/business context, the Mongoose-to-Beanie schema-sharing pipeline, and accepted interop gaps) into the current session. Use at the start of any session touching cross-repo interop among these three projects. Takes the common base folder path containing local clones of all three.
argument-hint: [base_path]
user-invocable: true
allowed-tools: Bash
---

## Arguments

| Variable | Description |
|----------|-------------|
| `$base_path` | Required. The common local folder under which all three project clones live. |

**`$base_path` is required.** If it is missing, stop and tell the user it wasn't provided before doing anything else.

## Project Map

All three paths below are resolved relative to `$base_path`:

| Project | Local Path |
|---|---|
| Jarvis Chat | `$base_path/jarvis-api` |
| Jarvis Registry | `$base_path/jarvis-registry` |
| Jarvis Registry CLI | `$base_path/jarvis-registry-cli` |

## Path Validation

Before injecting any context, verify all three paths above exist as directories. Run via `Bash`:

```
for d in jarvis-api jarvis-registry jarvis-registry-cli; do
  [ -d "$base_path/$d" ] || echo "MISSING: $base_path/$d"
done
```

If any path is reported missing, **stop immediately** and report to the user exactly which path(s) were not found. Do not proceed to inject context, and do not read from or reason about a project whose path is missing.

If all three exist, proceed to inject the context below.

## Context to Inject

### ASCENDING Inc. and its products

ASCENDING Inc. is a small consulting/staffing firm that also builds and sells AI software. Two flagship products, both sold B2B as single-tenant deployments (a dedicated k8s pod collection into the client's own cloud account — AWS EKS primarily, Azure AKS also supported, GCP nice-to-have). Target client size: ~300-500 employees. Business model: open-source the application code, close-source the deployment/IaC/CI-CD; revenue is a monthly subscription plus dedicated professional-services support and client-driven feature requests.

### The three projects

- **Jarvis Chat** ("Chat") — a LibreChat fork with light customization, at `$base_path/jarvis-api`. Already sold to several clients. Mostly just pulls in upstream fork source periodically — not actively changed by ASCENDING except where interop with Registry demands it.
- **Jarvis Registry** ("Registry") — at `$base_path/jarvis-registry`. Originally forked but now diverged very significantly (no fork-source pull since 2025-06); effectively ASCENDING's own product now.
- **Jarvis Registry CLI** — at `$base_path/jarvis-registry-cli`. The companion CLI to Registry: pulls ("sync-down") skills from Registry onto a user's local disk. Distributed to end users two ways: (1) a Homebrew tap and formula (`ascending-llc/homebrew-jarvis`), and (2) as a thin skill that wraps this CLI, itself shipped inside a Claude Code plugin. The sync destination is user-configured (`local.destination_folder` in `config.yaml`), typically pointed at that plugin's `skills/` folder.

### Why Chat and Registry must interoperate

ASCENDING's growth strategy is to cross-sell Registry to existing Chat clients (and use Chat to promote/demo Registry). Because of this, Chat and Registry are required to share a single MongoDB pod — even though the two codebases are otherwise unrelated and this is not an ideal architecture. **This is a deliberate, permanent constraint, not a temporary migration step**, and it applies uniformly across every collection under Registry's `_generated/` models (see below).

### The schema-sharing pipeline (Mongoose → Beanie)

Chat owns its MongoDB schemas, defined in TypeScript via Mongoose. A conversion pipeline at `$base_path/jarvis-api/.github/skills/mongoose-to-beanie/` converts those schemas into Python Beanie document classes, which are placed in Registry at `$base_path/jarvis-registry/registry-pkgs/src/registry_pkgs/models/_generated/`. Files in `_generated/` are regenerated from Chat's schemas and must not be hand-edited.

**Shared collections** (`_generated/`, as of the last regeneration):

| Collection | Generated class | Extended in Registry | Purpose |
|---|---|---|---|
| `accessroles` | `AccessRole` | Yes → `RegistryAccessRole` | RBAC role definitions (permission bits + resource type) |
| `aclentries` | `AclEntry` | Yes → `RegistryAclEntry` | Individual grants: principal × resource × role |
| `groups` | `Group` | No | Named groups of users |
| `keys` | `Key` | No | API keys issued to users |
| `mcpservers` | `MCPServer` | Yes → `ExtendedMCPServer` | Registered MCP server configs |
| `skills` | `Skill` | Yes → `ExtendedSkill` | A skill's metadata, body, and frontmatter |
| `skillfiles` | `SkillFile` | Yes → `ExtendedSkillFile` | Supporting files attached to a skill |
| `tokens` | `Token` | No | Encrypted OAuth access/refresh tokens and DCR client_id/client_secret pairs (`type` field: `mcp_oauth`, `mcp_oauth_refresh`, `mcp_oauth_client`, plus GitHub OAuth tokens for skill sync) |
| `users` | `User` | No | End-user accounts |

**Extending a shared collection:** if Registry needs its own fields on a shared collection, it adds a module under `$base_path/jarvis-registry/registry-pkgs/src/registry_pkgs/models/` that imports the `_generated` class and subclasses it (e.g. `ExtendedSkill(Skill)`). Registry also defines its own, non-shared Beanie document classes in that same `models/` directory (not under `_generated/`), e.g. `Federation`.

**How to apply — three hard constraints when touching these models:**
- An `Extended*` subclass may only add indices that don't already exist on the generated base class — adding a duplicate causes an "index already exists" error at Registry startup. A fully Registry-owned class (not extending anything shared) can define indices freely.
- Extending the `RegistryResourceType` enum (in `extended_access_role.py`) also requires updating the seeding script `$base_path/jarvis-registry/scripts/seed_access_roles_standalone.py` — otherwise the `accessroles` collection won't contain a seeded document for the new resource type.
- Any Registry read from `accessroles` or `aclentries` must filter on `resourceType`, or Beanie raises a validation error when it instantiates a document whose `resourceType` value falls outside Registry's narrower enum.

More generally: any change to a Registry Beanie model backed by a shared collection must preserve read/write compatibility with what Chat writes via its Mongoose schema, and vice versa where feasible.

### Skills sync scope (Jarvis Registry CLI)

The CLI's `sync-skills` command (`skills/client.go`) has a known, accepted scope gap: it does not parse or sync a skill's supporting files (`files[]`), and its list-skills call hardcodes `fileCount=0`. v1 only syncs skills that are a single flat `SKILL.md` with no supporting files. This is intentional, not a bug to flag: Registry stores supporting-file bytes inline (`skillfiles.$.body`), while Chat stores them via a pluggable external-storage adapter (S3 etc.) that Registry does not replicate, so each system shows "file not available" when it reads a `skillfiles` doc the other system created. Supporting files are also only rendered lazily in each product's frontend (on click, in a folder-tree view), so real-world impact is assumed low. Do not re-flag this as a bug unless the user explicitly decides to extend sync to skills-with-files.

## After Injecting

Confirm to the user in one or two sentences that the cross-project context has been loaded — name the three resolved paths and the core constraint (Chat and Registry share a single MongoDB pod, kept in schema sync via the Mongoose-to-Beanie generation pipeline) — without dumping the full text back out.
