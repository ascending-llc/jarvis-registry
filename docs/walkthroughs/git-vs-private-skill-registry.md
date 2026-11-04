---
title: Git repository or private agent skill registry?
description: Decide when Git is enough for private agent skills and when Jarvis Skill Gateway adds per-skill access and distribution across coding clients.
---

# Git repository or private agent skill registry?

**Git can be enough when one team reviews and shares the same agent skills through a repository everyone can access. A private skill registry becomes useful when the audience for each skill differs, developers use several coding clients, or the team needs a central place to control future downloads.** Keep Git for source review; add a registry for distribution when those needs arise.

[Jarvis Skill Gateway](../features/skill-gateway.md), part of ASCENDING's Jarvis Registry, provides a private catalog, skill versions and resource access controls. Its CLI synchronizes accessible skills into supported developer directories. It complements your repository review process rather than replacing it.

## Compare the responsibilities

| Team need | Git repository workflow | Git plus Jarvis Skill Gateway |
| --- | --- | --- |
| Review a skill change | Review the diff and approve a repository revision using your Git host's controls. | Keep that process, then import the approved revision into Registry. |
| Choose who can receive one skill | Use repository permissions, repository structure or additional automation. Repository access alone is not a per-skill Registry grant. | Grant access to the individual Registry skill; successful list and content reads require the relevant scope and resource permission. |
| Deliver skills to several coding clients | Maintain each client's layout and update process yourself. | Use the documented CLI modes for directory delivery; verify loading separately in each client. |
| Identify the delivered revision | Record the Git commit and track how it reached each developer. | Record the source commit, Registry skill ID and version, then compare the CLI manifest. These identifiers have different meanings. |
| Remove future access | Remove repository access and account for files already cloned or copied. | Remove all effective Registry grants. A successful CLI sync reconciles the managed copy; independent copies and loaded session content remain. |
| Authorize tools mentioned by a skill | Configure the referenced tool or service separately. | Do the same. Downloading a skill does not grant MCP or business-system access. |

A registry is an additional service to operate. If repository review and a small, consistent client setup meet your needs, another distribution layer may add more work than value. The [deployment guide](../deployment-guide.md) explains the infrastructure needed for Jarvis Registry.

## Follow one approved change from Git to a coding client

Use one skill with a supporting checklist so an update can be checked in both files:

```text
skills/
  release-notes-check/
    SKILL.md
    references/
      checklist.md
```

1. **Review in Git.** Approve a specific commit using your existing process. Skill Gateway supports Git providers including GitHub and GitLab; the [worked import example](private-skill-lifecycle.md#2-import-an-approved-private-revision) uses GitHub-specific configuration.
2. **Import into Registry.** For that example, configure `skills` as the container path. Record the job result and resolved commit, then inspect the imported skill and both files. A connected source is not proof of a completed import.
3. **Share the skill.** Give one test developer access to the imported skill and keep another excluded. Check direct, group, public and source grants.
4. **Sync to a client directory.** Configure and authenticate the CLI, select its documented directory mode, and sync to an empty disposable project. Check the files and manifest before assessing whether the AI client loaded the skill.
5. **Repeat after an update.** Import the newly approved Git commit, then sync again. Confirm both files changed and the recorded Registry version matches. Local edits are not pushed back to Git or Registry.

For commands, source fields and expected responses, use the complete [private skill lifecycle walkthrough](private-skill-lifecycle.md). It includes a two-file sample, a second revision and explicit access checks, so this decision guide does not require another parallel setup procedure.

## Example: one source repository, different skill audiences

Suppose a platform team reviews several skills in one private repository. Both engineering teams use that repository, but only one team should receive the release-note skill through Registry. Developers use a mix of Claude Code, Codex and Cursor.

Keep the Registry skill-sync source resource private to its import owner. Give the intended developer a direct grant on the imported release-note skill, then check an excluded developer with the same required API scope. Developers who can already read the Git repository retain that access and can copy its files; this example tests Registry distribution, not secrecy from repository readers. It isolates the per-skill distribution decision without changing the source-review process or granting both teams every imported skill. Check all inherited grants before treating the result as restricted access.

Use the [evaluation record template](examples/skills/registry-evaluation-record.json) to compare the two workflows. It is an editable evidence template, not a Jarvis configuration file or API request. Record each installed version, approved commit, skill ID, Registry version, client directory mode and observed result. Leave untested checks as `not_run`.

Choose the Registry workflow only if that extra access and delivery control is useful enough to operate the service. If everyone should receive the same small skill set and your Git-based delivery already works across clients, retain that simpler workflow.

## Test access removal before adopting the workflow

**Removing access prevents future Registry reads; it does not remotely erase every downloaded copy.** The CLI removes a previously managed skill after it successfully obtains the accessible-skill list and reconciles the local directory. A failed login or unavailable Registry is not a successful revocation check.

For a useful evaluation, test these cases with a harmless skill:

| Case | Result to check |
| --- | --- |
| Allowed and excluded developers | Both authenticate, but only the allowed developer can list and read the recorded skill ID. |
| Approved update | The same skill ID receives the imported revision; both files and the Registry version reach the managed directory. |
| Access removed | Registry list and content access change; a successful CLI sync removes the managed skill and manifest entry. |
| Independent copy | A copy saved outside the managed directory remains, showing the limit of local reconciliation. |
| Another source import | Removed access does not return through a remaining source or group grant. |

The [revocation test](private-skill-lifecycle.md#6-revoke-access-and-check-the-limits) explains source-to-skill grant inheritance. Removing a source grant alone does not remove child grants already copied to imported skills. A remaining source grant can add a missing child grant again on a later import.

## Keep skill distribution separate from MCP authorization

A skill package contains instructions and supporting files. An MCP server exposes tools a client can invoke. A private skill registry controls who can receive the package; an MCP gateway checks access to registered servers and routes permitted requests.

Jarvis Registry supports both capabilities, but their permissions do not substitute for each other. For example, distributing a release-note skill does not authorize a developer to query a company ticket system. Use the [MCP access-control walkthrough](mcp-access-control.md) to test the separate server-access boundary, including an explicit call by a user who knows the server ID and tool name but lacks the server grant.

## Check the implementation details

- [Skill Gateway](../features/skill-gateway.md): private catalog, Git-provider import and resource sharing.
- [AI Skills CLI](../features/ai-skills-cli.md): installation links, directory modes and client setup.
- [Skills API](../design/skills-sync-api.md): list/content authentication, versions and skill permissions.
- [Source sync API](../design/skill-sync-source-api.md): GitHub-specific fields, import containers and job responses.
- [RBAC and scopes](../design/scopes.md): API capabilities and resource permissions.
