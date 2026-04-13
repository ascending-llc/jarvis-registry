# MCP Gateway Registry - the registry-pkgs package

Unified vector database interface with three-layer architecture and model generation tools.

## Architecture

```
Repository (Type-safe Model API)
    ↓
VectorStoreAdapter (Proxy + Extension)
    ↓
LangChain VectorStore (Native DB)
```

**Key principles:**
- Maximize LangChain utilization
- Native filter support (no conversion)
- Direct method proxying
- Minimal abstraction

## Generating Models

Download pre-generated Beanie ODM models from jarvis-api GitHub releases.

### Prerequisites

Install GitHub CLI (optional but recommended for private repositories):

```bash
# macOS
brew install gh

# Authenticate with GitHub
gh auth login
```

### Setup

Run `uv sync` from project root, NOT this workspace member folder (`registry-pkgs`).

## Structure

```
registry-pkgs/src/registry_pkgs
├── models/                # Data models and schemas
│   ├── __init__.py        # Exports all models
│   ├── enums.py           # Enums (ToolDiscoveryMode, etc.)
│   └── _generated/        # Auto-generated models (gitignored)
│       ├── README.md      # Generation instructions
│       ├── .schema-version # Version tracking
│       └── *.py           # Generated Beanie models
├── vector/                # Vector database layer
│   ├── client.py          # DatabaseClient (facade)
│   ├── repository.py      # Generic Repository[T]
│   ├── adapters/
│   │   ├── adapter.py     # VectorStoreAdapter (base)
│   │   ├── factory.py     # Factory + registry
│   │   └── create/        # Creator functions
│   ├── backends/
│   │   ├── weaviate_store.py  # Weaviate implementation
│   │   └── chroma_store.py    # Chroma implementation
│   ├── config/            # Configuration classes
│   └── enum/              # Enums and exceptions
```

Model download implementation lives at `scripts/download_beanie_models.py` in the repository root.

## Configuration

```bash
# Required
VECTOR_STORE_TYPE=weaviate  # or chroma
EMBEDDING_PROVIDER=aws_bedrock  # or openai

# Weaviate
WEAVIATE_HOST=localhost
WEAVIATE_PORT=8099

# AWS Bedrock
AWS_REGION=us-east-1
EMBEDDING_MODEL=amazon.titan-embed-text-v2:0

# OpenAI
OPENAI_API_KEY=sk-...
OPENAI_MODEL=text-embedding-3-small
```

## Testing

If running from project root, use the following.

```bash
uv run --package registry-pkgs pytest registry-pkgs/tests/
```

If running from the workspace member directory `registry-pkgs`, use the following.

```bash
uv run poe test
```
