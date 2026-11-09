# Skill Gateway

The **Skill Gateway** is a private, organization-managed catalog for authoring, importing, governing, and distributing approved AI skills. Skills remain private by default and are shared through Registry ACLs; the [AI Skills CLI](ai-skills-cli.md) delivers accessible skills to supported developer tools without publishing private content to a public marketplace.

## Manage Skills in the Registry

Skills can be created and edited through the Registry UI or API. Teams can organize them with categories and tags, track a version, and enable or disable a skill as its availability changes. Each skill can include its `SKILL.md` content and supporting text or binary files.

Supporting files are validated before they are stored. Paths must be normalized relative POSIX paths, individual files are limited to 5 MiB, a skill can contain up to 200 files, and the total supporting-file payload is limited to 10 MiB. These limits keep skill content predictable for both the Registry and downstream sync clients.

## Govern Access with ACLs

Skills are private resources by default. The Registry applies resource-level ACL permissions so access can be scoped to the authenticated user and the individual skill:

| Permission | What it allows |
| --- | --- |
| **VIEW** | List, inspect, and download an accessible skill and its files |
| **EDIT** | Change skill metadata, content, and supporting files |
| **DELETE** | Remove the skill or its supporting files |
| **SHARE** | Manage access for other users or groups |

The skill creator receives full permissions, while other users receive only the access explicitly granted to them. Listing and content endpoints filter by ACL, so an inaccessible skill is not presented as an available delivery target.

<span id="import-from-github"></span>

## Import from Git Providers

Skill Gateway supports any Git provider, including GitHub and GitLab. Repository import moves skills in one direction: source repository to Registry. The Registry does not push changes back to the source repository.

The [GitHub source sync API](../design/skill-sync-source-api.md) documents the GitHub App connection and provider-specific fields used in the [private skill walkthrough](../walkthroughs/private-skill-lifecycle.md). These examples describe the GitHub integration rather than limiting Skill Gateway to one provider.

The documented GitHub import pipeline resolves the selected ref to a commit, downloads an immutable repository snapshot, and discovers skill folders containing `SKILL.md` in configured repository paths. It safely extracts those paths with protections for traversal, links, oversized files, and decompression bombs. Each discovered skill is parsed and validated before the sync applies the batch atomically; item-level errors are retained so operators can see which skills were skipped or failed without losing the job's overall report.

## Private Hosting and Governance

The enterprise benefit is controlled distribution:

- Keep organization-approved skills in a private Registry deployment.
- Scope access per user, group, and skill resource through ACLs.
- Protect source-provider and Registry credentials behind the configured authentication flow.
- Share approved content with developers without publishing private skills to a public marketplace.

## Deliver Skills to Developer Tools

Skill Gateway supports skill consumers including Claude Code, Codex, GitHub Copilot, and Cursor. The [AI Skills CLI](ai-skills-cli.md) delivers accessible Registry skills into personal or project environments using its documented directory modes. Use the client-specific setup for skill loading; the CLI repository contains the operational installation and command reference. MCP client connections are a separate capability.

## Common Questions

### What is a private skill marketplace?

A private skill marketplace is an organization-controlled catalog for approved skills, with access limited by the organization's policies. Jarvis Skill Gateway provides Registry-based authoring, Git-provider import, version and metadata management, and ACL-governed sharing rather than publishing private skills to a public marketplace.

### Which developer tools can receive Registry skills?

Supported consumers include Claude Code, Codex, GitHub Copilot, and Cursor. See [AI Skills CLI](ai-skills-cli.md) for the documented CLI modes and delivery workflow, and verify loading in the selected client. Skill delivery is distinct from connecting an MCP-compatible client to the gateway.

## Next Steps

- [AI Skills CLI](ai-skills-cli.md) - distribute Registry skills to local developer tools
- [RBAC & Scopes](../design/scopes.md) - understand identity scopes and access control
- [Security Control Design](../design/security-design.md) - see how authentication and ACLs compose
- [GitHub source sync API](../design/skill-sync-source-api.md) - configure repository import and inspect sync jobs
- [Private skill lifecycle walkthrough](../walkthroughs/private-skill-lifecycle.md) - verify import, updates, and access removal
