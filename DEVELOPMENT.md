# Development Guide

This guide covers the development workflow for the MCP Gateway project.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) - Fast Python package manager

## Initial Setup

```bash
# Clone the repository
git clone <repository-url>
cd mcp-gateway

# Install all dependencies (including dev tools)
uv sync --all-groups

# Install pre-commit hooks (runs automatically on every commit)
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

```bash
# Install hooks (one-time setup)
uv run poe hooks-install

# Run hooks manually on all files
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

### Bypassing Hooks (Emergency Only)

If you need to commit without running hooks (not recommended):

```bash
git commit --no-verify -m "message"
```

Note: CI will still catch any issues, so this just delays the fix.

## How the Double Defense Works

```
Developer Machine                     GitHub CI
       |                                  |
       v                                  v
  git commit                         push / PR
       |                                  |
  Pre-commit hooks                   Lint job runs
  run automatically                  ruff check
       |                                  |
  [BLOCKS commit                     [FAILS PR if
   if errors]                         errors found]
       |                                  |
       v                                  v
  Clean commit  ------------------>  Merge allowed
```

| Stage | Action | Result of Failure |
|-------|--------|-------------------|
| Local (Pre-commit) | Runs on `git commit` | Blocks the commit |
| CI (GitHub Actions) | Runs on push/PR | Fails the build, blocks merge |

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
```

## Project Structure

```
mcp-gateway/
├── registry/          # Main registry service
├── auth-server/       # Authentication service
├── registry-pkgs/     # Shared packages
├── servers/           # MCP servers
├── frontend/          # Web UI
├── docs/              # Documentation
└── pyproject.toml     # Root workspace config
```

## IDE Setup

### VS Code

Recommended extensions:
- [Ruff](https://marketplace.visualstudio.com/items?itemName=charliermarsh.ruff) - Linting and formatting
- [Python](https://marketplace.visualstudio.com/items?itemName=ms-python.python) - Python support

Settings (`.vscode/settings.json`):
```json
{
  "[python]": {
    "editor.defaultFormatter": "charliermarsh.ruff",
    "editor.formatOnSave": true,
    "editor.codeActionsOnSave": {
      "source.fixAll.ruff": "explicit",
      "source.organizeImports.ruff": "explicit"
    }
  }
}
```

### PyCharm

1. Install the [Ruff plugin](https://plugins.jetbrains.com/plugin/20574-ruff)
2. Enable "Format on Save" in Preferences > Tools > Ruff

## Troubleshooting

### Pre-commit hook fails

```bash
# See what's wrong
uv run poe check

# Auto-fix issues
uv run poe fix

# Then commit again
git add -A && git commit -m "message"
```

### Ruff not finding config

Ensure you're running from the repository root:
```bash
cd /path/to/mcp-gateway
uv run ruff check .
```

### Dependencies out of sync

```bash
uv sync --all-groups
```
