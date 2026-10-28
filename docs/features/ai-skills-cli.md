# AI Skills CLI

The **Jarvis Registry CLI** is the secure delivery layer from private Registry skills to local [Claude Code](https://docs.anthropic.com/en/docs/claude-code/skills), [Codex](https://developers.openai.com/codex/skills/), and [GitHub Copilot](https://docs.github.com/en/copilot/concepts/agents/about-agent-skills) environments. It lets developers consume organization-approved skills without manually copying content or handling Registry credentials in scripts.

The [Skill Gateway](skill-gateway.md) is where skills are authored, imported, governed, and shared. The CLI is where an authenticated developer reconciles the skills they can access into a local integration.

For installation, platform requirements, configuration, command reference, and troubleshooting, see the [Jarvis Registry CLI repository](https://github.com/ascending-llc/jarvis-registry-cli).

## Shared Workflow

Every integration follows the same three steps:

```sh
jarvis-registry configure
jarvis-registry auth login
jarvis-registry skills sync --mode claude
```

This example selects Claude Code. Use `--mode codex` or `--mode copilot` for the other supported integrations.

The CLI authenticates against the Registry and syncs only the skills available to the signed-in user. Browser sign-in normally stores credentials in the operating system keyring. On Linux without a usable keyring, it falls back to a plaintext `~/.jarvis-registry/credentials.json` file with mode `0600` and a first-use warning. The explicit `local.credentials.file_only: true` option also uses that file; it is not encrypted. Review the [credential-storage options](https://github.com/ascending-llc/jarvis-registry-cli/blob/main/docs/getting-started.md) for the machine running the CLI.

## Supported Integrations

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code/skills)
- [Codex](https://developers.openai.com/codex/skills/)
- [GitHub Copilot](https://docs.github.com/en/copilot/concepts/agents/about-agent-skills)

Skills can be synchronized for personal use across projects or for a specific project. The CLI handles the integration-specific local layout and invocation conventions for each supported tool.

## Governed Delivery

Sync is access-aware and repeatable: after a successful sync, the CLI delivers newly accessible skills, updates changed content, and removes managed local skills the user can no longer access. Revoking Registry access does not instantly recall files already copied to a developer's machine, and the CLI cannot remove independent copies outside its managed paths.

Local cleanup depends on the integration and scope. Personal-scoped Codex and Copilot sync leave skill-name collisions untouched by default; choosing an interactive replacement or override can remove the conflicting directory's contents. Those replacement options do not apply to Claude or project scope. Project-scoped Codex and Copilot sync treat their managed directories as CLI-owned and remove entries not in the current managed set. Review the [CLI getting started guide](https://github.com/ascending-llc/jarvis-registry-cli/blob/main/docs/getting-started.md) before scheduling sync or using replacement options.

## Learn More

- [Jarvis Registry CLI repository](https://github.com/ascending-llc/jarvis-registry-cli) - product source and full documentation
- [CLI setup guide](https://github.com/ascending-llc/jarvis-registry-cli/blob/main/docs/setup.md) - installation and configuration
- [CLI getting started guide](https://github.com/ascending-llc/jarvis-registry-cli/blob/main/docs/getting-started.md) - integration workflow and sync behavior

## Next Steps

- [Skill Gateway](skill-gateway.md) - author, import, govern, and share the skills delivered by the CLI
- [Private skill lifecycle walkthrough](../walkthroughs/private-skill-lifecycle.md) - verify import, updates, and access removal
