# Documentation

This directory contains the MkDocs-based documentation for the MCP Gateway & Registry.

## Building Documentation Locally

### Prerequisites

```bash
# Using uv (recommended)
uv pip install -e ".[docs]"

# Or using pip
pip install -e ".[docs]"
```

### Development Server

```bash
# Start development server with live reload
mkdocs serve

# The documentation will be available at http://127.0.0.1:8000
```

### Building Static Site

```bash
# Build static site
mkdocs build

# The built site will be in the `site/` directory
```

## Documentation Structure

- `index.md` - Main landing page (generated from README.md)
- `installation.md` - Complete installation guide
- `quick-start.md` - Quick start tutorial
- [IDP Integration](features/auth-server.md) - Identity provider setup and authentication
- `cognito.md` - Amazon Cognito configuration
- `scopes.md` - Access control and permissions
- `registry_api.md` - API reference
- `dynamic-tool-discovery.md` - AI agent tool discovery
- `ai-coding-assistants-setup.md` - IDE integration guide
- `FAQ.md` - Frequently asked questions

## Deployment

The documentation is automatically deployed to GitHub Pages when changes are pushed to the `main` branch via GitHub Actions.

### Manual Deployment

```bash
# Deploy to GitHub Pages
mkdocs gh-deploy
```

## Theme and Configuration

The documentation uses the [Material for MkDocs](https://squidfunk.github.io/mkdocs-material/) theme with:

- Light/dark mode toggle
- Navigation tabs and sections
- Search functionality
- Code syntax highlighting
- Mermaid diagram support
- Git revision dates

## Contributing

When adding new documentation:

1. Create markdown files in the appropriate directory
2. Update `mkdocs.yml` navigation structure
3. Use proper markdown formatting and admonitions
4. Include code examples where relevant
5. Test locally with `mkdocs serve` before committing

## Plugins Used

- **search** - Full-text search functionality
- **git-revision-date-localized** - Shows last update dates
- **minify** - Minifies HTML output for production
- **pymdown-extensions** - Enhanced markdown features
