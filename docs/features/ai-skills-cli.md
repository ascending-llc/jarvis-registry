# AI Skills CLI

The **Jarvis Registry CLI** delivers accessible private Registry skills into local developer environments. It lets developers consume organization-approved skills without manually copying content or handling Registry credentials in scripts. Skill Gateway supports consumers including Claude Code, Codex, GitHub Copilot, and Cursor; this page describes the CLI's documented directory modes.

The [Skill Gateway](skill-gateway.md) is where skills are authored, imported, governed, and shared. The CLI is where an authenticated developer reconciles the skills they can access into a local integration.

For installation, platform requirements, configuration, command reference, and troubleshooting, see the [Jarvis Registry CLI repository](https://github.com/ascending-llc/jarvis-registry-cli).

## Shared Workflow

Configure the Registry and sign in:

```sh
jarvis-registry configure
jarvis-registry auth login
```

Choose the sync mode for your client. The command syntax is:

```text
jarvis-registry skills sync --mode <claude|codex|copilot>
```

Replace the placeholder with one mode. For example, to sync for Claude Code:

```sh
jarvis-registry skills sync --mode claude
```

Use `--mode codex` for Codex or `--mode copilot` for GitHub Copilot. These mode names select local directory conventions; they are not an exhaustive list of Skill Gateway consumers.

The CLI authenticates against the Registry and syncs only the skills available to the signed-in user. Browser sign-in normally stores credentials in the operating system keyring. On Linux without a usable keyring, it falls back to a plaintext `~/.jarvis-registry/credentials.json` file with mode `0600` and a first-use warning. The explicit `local.credentials.file_only: true` option also uses that file; it is not encrypted. Review the [credential-storage options](https://github.com/ascending-llc/jarvis-registry-cli/blob/main/docs/getting-started.md) for the machine running the CLI.

## Consumers and CLI Modes

| Consumer | Documented CLI mode |
| --- | --- |
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code/skills) | `claude` |
| [Codex](https://developers.openai.com/codex/skills/) | `codex` |
| [GitHub Copilot](https://docs.github.com/en/copilot/concepts/agents/about-agent-skills) | `copilot` |

Cursor is also a supported Skill Gateway consumer. Its [Agent Skills guide](https://cursor.com/docs/skills) includes project `.agents/skills/`, the project destination written by `codex` mode. Verify skill loading in your Cursor version. The CLI command reference documents the three modes above; it does not define a separate `cursor` mode.

For these CLI modes, skills can be synchronized for personal use across projects or for a specific project. The CLI handles the local layout and invocation conventions for each mode.

## Claude Code MCP Connection

Claude-mode sync also writes the Registry MCP connection into the generated plugin's `.claude-plugin/plugin.json`. Run `jarvis-registry skills sync --mode claude` for the personal plugin at `~/.claude/skills/jarvis-registry/`, or add a project path for `<path>/.claude/skills/jarvis-registry/`. Configure `registry.base_url` as the deployment origin; the CLI appends `/gateway/proxy/mcpgw/mcp` and includes no credentials in the manifest.

Automatic loading of plugins from skills directories requires Claude Code v2.1.157 or later; see the [Claude Code release notes](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md#21157). After the first sync or a connection change, reload the plugin or start a new Claude Code session. For a project plugin, start Claude Code from that project directory and complete any workspace trust and MCP approval. Open `/mcp`, select the plugin's Registry connection, and complete MCP OAuth if prompted. This authorization is separate from the CLI's skills login. Confirm that the plugin's discovery tools are listed and that a read-only discovery call succeeds; there is no need to add a separate manual MCP definition.

Existing manually configured connections remain untouched. If a duplicate appears, temporarily isolate it while checking the plugin connection and use the tool names actually displayed by Claude Code. See the [CLI getting started guide](https://github.com/ascending-llc/jarvis-registry-cli/blob/main/docs/getting-started.md#claude-code) for personal/project loading, plugin precedence, authentication, and sync verification steps.

Codex and Copilot modes continue to deliver skills; they do not install MCP plugins through this sync flow.

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
