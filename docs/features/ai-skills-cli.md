# AI Skills CLI

The **Jarvis Registry CLI** is the secure delivery layer from private Registry skills to local [Claude Code](https://docs.anthropic.com/en/docs/claude-code/skills), [Codex](https://developers.openai.com/codex/skills/), and [GitHub Copilot](https://docs.github.com/en/copilot/concepts/agents/about-agent-skills) environments. It lets developers consume organization-approved skills without manually copying content or handling Registry credentials in scripts.

The [Skill Gateway](skill-gateway.md) is where skills are authored, imported, governed, and shared. The CLI is where an authenticated developer reconciles the skills they can access into a local integration.

For installation, platform requirements, configuration, command reference, and troubleshooting, see the [Jarvis Registry CLI repository](https://github.com/ascending-llc/jarvis-registry-cli).

## Shared Workflow

Every integration follows the same three steps:

```sh
jarvis-registry configure
jarvis-registry auth login
jarvis-registry skills sync --mode <claude|codex|copilot>
```

The CLI stores credentials in the operating system keyring, authenticates against the Registry, and syncs only the skills available to the signed-in user.

## Supported Integrations

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code/skills)
- [Codex](https://developers.openai.com/codex/skills/)
- [GitHub Copilot](https://docs.github.com/en/copilot/concepts/agents/about-agent-skills)

Skills can be synchronized for personal use across projects or for a specific project. The CLI handles the integration-specific local layout and invocation conventions for each supported tool.

## Governed Delivery

Sync is access-aware and repeatable: the CLI delivers newly accessible skills, updates changed content, and reconciles managed skills when access changes. This gives developers a consistent local experience while the Registry remains the source of truth for approved content.

The CLI also protects local content from unintended collisions and supports repeatable execution, including scheduled sync workflows. See the [CLI getting started guide](https://github.com/ascending-llc/jarvis-registry-cli/blob/main/docs/getting-started.md) for integration-specific behavior.

## Learn More

- [Jarvis Registry CLI repository](https://github.com/ascending-llc/jarvis-registry-cli) - product source and full documentation
- [CLI setup guide](https://github.com/ascending-llc/jarvis-registry-cli/blob/main/docs/setup.md) - installation and configuration
- [CLI getting started guide](https://github.com/ascending-llc/jarvis-registry-cli/blob/main/docs/getting-started.md) - integration workflow and sync behavior

## Next Steps

- [Skill Gateway](skill-gateway.md) - author, import, govern, and share the skills delivered by the CLI
