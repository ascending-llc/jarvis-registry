# Frontend Testing via AI Skills

The `local-frontend-check` Claude skill lets you ask AI (in natural language) to navigate
the Jarvis Registry UI, perform operations, and verify results — while it cross-checks backend
logs automatically. It is intended for manual regression checks, bug-fix verification, and
end-to-end confirmation of specific flows without running the full automated test suite.

---

## Prerequisites

### 1. Install `playwright-cli`

`playwright-cli` must be available as a global command. Install it via Homebrew like below.

```bash
brew install playwright-cli
```

If Homebrew is not available, use `npm install -g`.

```bash
npm install -g @playwright/cli@latest
```

Verify:

```bash
playwright-cli --version   # should print 0.1.7 or later
```

Install `playwright-cli` skills into the `.claude/skills/` folder of this project.
The `.claude/` folder is not Git tracked.

```bash
playwright-cli install --skills
```

### 2. Make the skill available to your editor

The skill lives in `.github/skills/local-frontend-check/`.
After creating a symlink into it from `.claude/skills/`,
Claude Code CLI picks it up automatically.
When using VS Code Copilot or Claude Desktop, you need to tell it the explicit path
to the skill folder.

---

## Symlinking for Claude Code CLI

Claude Code CLI looks for skills relative to the workspace root.
Create a symlink so Claude Code can find the `.claude/` directory:

```bash
# From project root
mkdir -p .claude/skills
pushd .claude/skills
ln -s ../../.github/skills/local-frontend-check/
popd
```

---

## How to invoke the skill

Before you invoke the skill, you should start the local services via `docker compose`,
and check that you can access Registry frontend at `http://localhost/gateway`.
In particular, in your `.env` file the value of `REGISTRY_CLIENT_URL` should have a `/gateway`
path portion.

```
REGISTRY_CLIENT_URL=http://localhost:80/gateway
```

### Claude Code CLI

Prefix your request with `/local-frontend-check`:

```
/local-frontend-check Navigate to the MCP Servers list and verify that "my-server" appears with status Enabled.
```

```
/local-frontend-check Delete the federation named "adhoc-test" and verify success.
```

Claude will open a headed Chrome window, navigate the UI, perform the requested operations,
read backend logs from `jarvis-registry-registry-1`, and report a `PASS` / `FAIL` verdict.

### VS Code Copilot (chat panel)

VS Code Copilot has no equivalent of invoking the `/local-frontend-check` skill as in Claude Code CLI. You must tell it the explicit path to the skill folder `.github/skills/local-frontend-check/`,
the explicit path to the `playwright-cli` skill folder `.claude/skills/playwright-cli/`,
and then give it natural language prompts.

It's recommended to use a Claude model to do this.

---

## First-run authentication

The first time the skill runs (or after a session expiry), the browser will land on the login
page. Claude Code will pause and ask you to log in manually in the headed window. Once you confirm,
it continues from where it left off.

---

## Session persistence

The `jarvis` browser session uses a **persistent profile** stored on disk:

```
~/Library/Caches/ms-playwright/daemon/<daemon-id>/ud-jarvis-undefined/
```

Because the profile is saved between runs, your login cookies survive across invocations.
Subsequent skill runs skip the login step entirely as long as the session has not expired.

### Checking session state

```bash
playwright-cli list
```

Possible statuses:

| Status | Meaning |
|---|---|
| `open` | Browser window is running |
| `closed` | Window was closed; profile data still on disk, daemon record retained |

### Closing a session

If you close the browser window manually, the daemon record lingers. Clean it up with:

```bash
playwright-cli -s=jarvis close
```

### Deleting profile data

To wipe the saved login cookies and force a fresh login on the next run:

```bash
playwright-cli -s=jarvis delete-data
```

---

## Testing artifacts

Every skill run writes artifacts to `.playwright-cli/` at the **repo root**. This directory is
gitignored (see `.gitignore`).

| File pattern | Contents |
|---|---|
| `page-<timestamp>.png` | Screenshots captured with `playwright-cli screenshot` |
| `page-<timestamp>.yml` | Accessibility-tree snapshots (used internally for element targeting) |
| `console-<timestamp>.log` | Browser console output (errors, warnings) |

Screenshots are the most useful artifacts for human review — they capture the UI state at key
moments (after a save, after a delete, on an error). Snapshot YAML files are text-only
structural dumps used by the skill to locate elements by ref; you rarely need to read them
directly.

Artifacts accumulate over time. Delete them when no longer needed:

```bash
rm -rf .playwright-cli/
```
