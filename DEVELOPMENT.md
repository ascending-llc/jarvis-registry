# Development Guide

This guide covers the development workflow for Jarvis Registry.

## Prerequisites

- Python 3.12 (the root `pyproject.toml` requires `>=3.12,<3.13`)
- [uv](https://docs.astral.sh/uv/) - Fast Python package manager
- Node.js 22 (frontend; matches `docker/Dockerfile.registry-frontend`)
- Docker with Compose (local end-to-end runs use `docker-compose.yml`)

## Initial Setup

```bash
# Clone the repository
git clone https://github.com/ascending-llc/jarvis-registry.git
cd jarvis-registry

# Install all dependencies (including dev tools)
uv sync --all-groups

# Install pre-commit and post-merge hooks
uv run poe hooks-install
```

## Code Quality

This project uses [Ruff](https://docs.astral.sh/ruff/) for linting and formatting. Ruff replaces multiple tools (black, isort, flake8) with a single, fast tool.

### Available Commands

| Command | Description |
|---------|-------------|
| `uv run poe lint` | Run linter (check for errors) |
| `uv run poe lint-fix` | Run linter with auto-fix |
| `uv run poe format` | Format code |
| `uv run poe format-check` | Check formatting without changes |
| `uv run poe check` | Run all checks (lint + format) |
| `uv run poe fix` | Fix all auto-fixable issues and format |

### Pre-commit Hooks

Pre-commit hooks run automatically when you commit code. They catch issues before they reach CI.

Install them once with `uv run poe hooks-install` (see Initial Setup). To run them manually on all files:

```bash
uv run poe hooks-run
```

**What the hooks check:**
- Ruff linting (with auto-fix)
- Ruff formatting
- Trailing whitespace
- End-of-file newlines
- YAML syntax
- Large files (>1MB)
- Merge conflicts
- Private keys (security)
- Bandit security scanning
- Tartufo credential scanning

After a `git pull`, the post-merge hooks run `uv sync` if `uv.lock` changed and `npm ci` in `frontend/` if `frontend/package-lock.json` changed.

### Bypassing Hooks (Emergency Only)

If you need to commit without running hooks (not recommended):

```bash
git commit --no-verify -m "message"
```

Note: CI re-runs the ruff lint and format checks and the tests, so those issues still block the PR. CI does not run tartufo, so a skipped credential scan is not caught later. CI's bandit step only uploads a report and never fails the build, because its findings are mostly false positives.

## How the Double Defense Works

| Stage | Action | Result of Failure |
|-------|--------|-------------------|
| Local (Pre-commit) | Runs on `git commit` | Blocks the commit |
| CI (GitHub Actions) | Runs ruff and tests on PRs and pushes to `main` | Fails the build, blocks merge |

## Running Tests

```bash
# Run all tests
uv run poe test-all

# Run with coverage
uv run poe test-all-cov

# Run specific project tests
uv run poe test-registry
uv run poe test-auth-server
uv run poe test-registry-pkgs
uv run poe test-workflow-worker
```

## Project Structure

```
jarvis-registry/
├── registry/          # Main registry service
├── auth-server/       # Authentication service
├── workflow-worker/   # Workflow scheduler and executor
├── registry-pkgs/     # Shared packages
├── frontend/          # Web UI
├── docker/            # Dockerfiles
├── config/            # Observability config (Prometheus, Grafana, OTel, Tempo)
├── scripts/           # Utility scripts
├── docs/              # Documentation
└── pyproject.toml     # Root workspace config
```

## GitNexus (code intelligence for agents)

CI re-indexes `main` with GitNexus on every push that changes code (pushes touching only docs, Markdown or `.github/` are skipped), and the index is served as a remote MCP server; nobody runs GitNexus locally. Agents reach it two ways:

- **Through jarvis-registry (recommended)**: add jarvis-registry as an MCP server in your agent. The GitNexus tools then show up through its discovery tools and use the registry's auth.
- **Directly**: connect to the hosted runtime with a bearer token.

The exposed tools are `gitnexus_impact`, `gitnexus_query`, `gitnexus_context`, `gitnexus_detect_changes`, `gitnexus_rename`, `gitnexus_cypher`, `gitnexus_route_map`, `gitnexus_tool_map`, `gitnexus_shape_check`, `gitnexus_api_impact` and `list_repos`, plus the `gitnexus://repo/jarvis-registry/...` resources. `gitnexus_detect_changes` and `gitnexus_rename` don't work remotely; `AGENTS.md` covers how agents should use the rest.

## Troubleshooting

### Pre-commit hook fails

```bash
# See what's wrong
uv run poe check

# Auto-fix issues
uv run poe fix

# Re-stage the files the hooks fixed, then commit again
git add <files>
git commit -m "message"
```

### Ruff not finding config

Ensure you're running from the repository root:
```bash
cd /path/to/jarvis-registry
uv run ruff check .
```

### Dependencies out of sync

```bash
uv sync --all-groups
```
