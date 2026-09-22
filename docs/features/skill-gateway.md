# Skill Gateway

The Skill Gateway privately hosts organization-curated AI skills, governs who can access them, and distributes approved content consistently to the developer tools your teams already use. Platform teams get one Registry control plane for authoring, importing, managing, and sharing skills without publishing private content publicly.

## Manage Skills in the Registry

Skills can be created and edited through the Registry UI or API. Teams can organize them with categories and tags, track a version, and enable or disable a skill as its availability changes. Each skill can include its `SKILL.md` content and supporting text or binary files.

Supporting files are validated before they are stored. Paths must be normalized relative POSIX paths, individual files are limited to 5 MiB, a skill can contain up to 50 files, and the total supporting-file payload is limited to 10 MiB. These limits keep skill content predictable for both the Registry and downstream sync clients.

## Govern Access with ACLs

Skills are private resources by default. The Registry applies resource-level ACL permissions so access can be scoped to the authenticated user and the individual skill:

| Permission | What it allows |
| --- | --- |
| **VIEW** | List, inspect, and download an accessible skill and its files |
| **EDIT** | Change skill metadata, content, and supporting files |
| **DELETE** | Remove the skill or its supporting files |
| **SHARE** | Manage access for other users or groups |

The skill creator receives full permissions, while other users receive only the access explicitly granted to them. Listing and content endpoints filter by ACL, so an inaccessible skill is not presented as an available delivery target.

## Import from GitHub

GitHub is the supported source for repository import. A GitHub App or OAuth connection lets the Registry read a repository and import skills in one direction: repository to Registry. The Registry does not push changes back to GitHub.

The import pipeline resolves the selected ref to a commit, downloads an immutable repository snapshot, and discovers skill folders containing `SKILL.md`. It safely extracts configured paths with protections for traversal, links, oversized files, and decompression bombs. Each discovered skill is parsed and validated before the sync applies the batch atomically; item-level errors are retained so operators can see which skills were skipped or failed without losing the job's overall report.

GitHub sync supports repository paths configured for skill discovery, with the Registry as the managed destination for imported skills.

## Private Hosting and Governance

The enterprise benefit is controlled distribution:

- Keep organization-approved skills in a private Registry deployment.
- Scope access per user, group, and skill resource through ACLs.
- Protect GitHub and Registry credentials behind the configured authentication flow.
- Share approved content with developers without publishing private skills to a public marketplace.

## Deliver Skills to Developer Tools

The [AI Skills CLI](ai-skills-cli.md) is the delivery layer for accessible Registry skills. It syncs approved content into personal or project environments for Claude Code, Codex, and GitHub Copilot. The CLI repository contains the operational installation and command reference.

## Next Steps

- [AI Skills CLI](ai-skills-cli.md) - distribute Registry skills to local developer tools
- [RBAC & Scopes](../design/scopes.md) - understand identity scopes and access control
- [Security Control Design](../design/security-design.md) - see how authentication and ACLs compose
- [GitHub source sync API](../api-reference.md) - review the Registry API surface
