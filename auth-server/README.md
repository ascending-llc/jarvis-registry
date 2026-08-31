# Auth Server

OAuth 2.0 Authorization Server with Device Flow support (RFC 8628) and OIDC Discovery.

## Features

- ✅ **OAuth 2.0 Device Authorization Grant** (RFC 8628)
- ✅ **OAuth 2.0 Authorization Server Metadata** (RFC 8414)
- ✅ **OpenID Connect Discovery**
- ✅ **JWKS Endpoint** for token verification
- ✅ **Multi-provider support**: Keycloak, AWS Cognito, Azure Entra ID
- ✅ **FastAPI** with automatic API documentation

## Local Development Setup

### Prerequisites

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) package manager

### Setup

Run `uv sync` from project root, NOT this workspace member folder (`auth-server`).

### Environment Variables

Create a `.env` file at the **project root** (`cp .env.example .env` from the repo root) — this
is the same file docker-compose uses (`env_file: - .env`) for every service, including
`auth-server`.

### Running the Server

From the **project root** (not this `auth-server` directory — `AuthSettings` reads `.env` relative
to the current working directory), run the following command.

```bash
# Development mode with auto-reload
uv run uvicorn auth_server.server:app --reload --host 0.0.0.0 --port 8888
```

The server will be available at:
- API: http://localhost:8888
- Interactive docs: http://localhost:8888/docs
- Alternative docs: http://localhost:8888/redoc

## Testing

The test suite uses `pytest` with `poethepoet` (`poe`) for task management.

### Run Tests

If running from project root, use the following.

```bash
uv run --package auth-server pytest auth-server/tests/
```

If running from the workspace member directory `auth-server`, use the following.

```bash
# Run all tests
uv run poe test

# Run all tests with coverage report
uv run poe test-cov
```
