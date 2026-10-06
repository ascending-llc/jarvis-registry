# Jarvis Registry — Agent Guide

Python 3.12 monorepo (FastAPI, Beanie on MongoDB, Weaviate, Redis) plus a React frontend. All Python workspaces are managed by `uv` from the root `pyproject.toml`.

| Workspace | Depends on | Purpose |
|---|---|---|
| `registry/` | `registry-pkgs` | Main backend: REST API and MCP proxy |
| `auth-server/` | `registry-pkgs` | OAuth2/OIDC auth server (Entra ID, Google) |
| `workflow-worker/` | `registry-pkgs` | Workflow scheduler and executor |
| `registry-pkgs/` | — | Shared Beanie models, DB/Redis clients, vector search, telemetry, migrations |
| `frontend/` | — | SPA (Vite, React 18, TailwindCSS, Biome) |

---

## Design and Review Discussions

When reviewing a plan, spec or change with the developer, first ask which mode they want (unless the command or skill in use defines its own format):

1. **BIG CHANGE**: work through the review stages one at a time.
2. **SMALL CHANGE**: raise only the single most important question per stage.

### Review stages

Go through these in order. Skip a stage that has no issues, and say that you skipped it.

1. **Architecture**: component boundaries, coupling, data flow, scaling and single points of failure, security (auth, data access, API boundaries).
2. **Code quality**: module structure, duplication, error handling and missing edge cases, technical debt, over- or under-engineering.
3. **Tests**: coverage gaps, assertion strength, untested edge cases and failure paths.
4. **Performance**: N+1 queries and database access patterns, memory use, caching, expensive code paths.

After each stage, stop and wait for the developer's answers before starting the next one. Don't assume priorities on timeline or scale.

### Raising issues

- Rank issues by severity. Discuss the top ones in full and list the rest in one line each.
- For each issue, describe the problem concretely with `file:line` references, then give 2–3 options, including "do nothing" where reasonable. For each option, state effort, risk, impact on other code and maintenance burden.
- Put the recommended option first and explain why. When options are close, prefer the one that removes duplication, handles more edge cases, and is explicit rather than clever.
- Number issues and letter options so the developer can answer "2B". Format each issue like this:

```markdown
**Issue 1: {problem}**

- **A. {option} (recommended)**
  - Tradeoffs: {effort / risk / impact}
- **B. {option}**
  - Tradeoffs: {effort / risk / impact}

**Recommendation:** A, because {reason}.
```

---

## Workspace Boundaries

- **Beanie Document models** live only in `registry-pkgs/src/registry_pkgs/models/`, never in `registry/`, `auth-server/` or `workflow-worker/`. API request/response models go in `registry/src/registry/schemas/`.
- **Dependencies flow one way**: `registry`, `auth-server` and `workflow-worker` each depend on `registry-pkgs`, and never import from each other.
- **Route handlers are thin**: `registry/src/registry/api/` holds route definitions only. Business logic and database access live in `services/`.
- **Frontend uses Biome** for formatting and linting, not ruff, ESLint or Prettier.

---

## Code Conventions

### Python
- **Tooling**: `uv` (never `pip`), FastAPI, Pydantic models for request/response and config (`BaseSettings` for env-loaded config), ruff for lint and format.
- **Async**: use `async`/`await` for all I/O. Never block the event loop.
- **Type hints** on all functions and methods. Annotate nullable values as `type | None`.
- **Constants**: name non-obvious literals as module-level constants. Put constants shared across modules in `registry/src/registry/constants.py`.
- **Logging**: use a module-level `logger = logging.getLogger(__name__)`; logging is already configured centrally. Never log passwords, tokens or PII.

### Error handling
- Catch specific exception types (no bare `except:`), and chain re-raised exceptions with `from e`.
- **Response shape**: the Registry's own REST API returns FastAPI's `{"detail": "..."}`. Protocol endpoints follow their spec instead: OAuth/OIDC endpoints in `auth-server` (RFC 6749 `error` / `error_description`), MCP proxy routes (JSON-RPC errors), and the MCP registry spec routes in `mcp_registry_routes.py` (problem+json).
- **Services** signal invalid input either by raising `HTTPException(4xx)` directly or by raising `ValueError` (or a module-specific exception) that the route converts to a 4xx. Other exceptions (database errors, unexpected failures) propagate.
- **Routes** catch service exceptions, log them, re-raise `HTTPException(4xx)` as-is, convert `ValueError` (and other known input errors) from services to 4xx, and wrap anything else in `HTTPException(500, detail="Internal server error")`. Don't raise `ValueError` from a route handler for a server-side failure: the handler's own `except ValueError` turns it into a 400.

### TypeScript (frontend)
- Prefer precise types over `any`. Biome's `noExplicitAny` is currently off, so this isn't enforced.

---

## Development Commands

Run from the repo root unless noted. Local end-to-end testing uses `docker compose` (`docker-compose.yml`).

| Command | Purpose |
|---|---|
| `uv sync --all-groups` | Install all dependencies, including dev tools |
| `uv run poe test-all` | Run tests in every Python workspace |
| `uv run poe test-<workspace>` | Run one workspace's tests (`registry`, `auth-server`, `registry-pkgs`, `workflow-worker`) |
| `uv run poe check` | Lint and format check (what CI runs) |
| `uv run poe fix` | Auto-fix lint issues and format |
| `uv run poe hooks-install` | Install pre-commit and post-merge hooks |

Frontend (from `frontend/`): `npm start` (Vite dev server), `npm run build`, `npm test`, `npm run format` (Biome).

### Before committing
- Run `uv run poe fix`, then the touched workspace's tests (see § Testing).
- When a change removes or renames a symbol, `git grep` for remaining references.
- Pre-commit hooks run ruff, bandit and tartufo on `git commit`. Handle bandit false positives with `# nosec` and a justification.

---

## Data Migrations

**Before writing or reviewing a migration, read `registry-pkgs/src/registry_pkgs/migrations/README.md`.**

- **Location and naming**: `registry-pkgs/src/registry_pkgs/migrations/versions/m<NNNN>_<name>.py`, next free number, `[a-z0-9_]` name. It defines `async def up(db: AsyncDatabase) -> None`.
- **Contract**: raw pymongo through `db` only, never Beanie `Document` classes. Idempotent: safe to re-run after a full or partial run, while old pods, new pods and Jarvis Chat write the same collections concurrently. Compatible with the previous release's code (rollouts overlap old and new pods). Never blocks the event loop (no sync I/O, yield in long loops): that starves the lock heartbeat.
- **Records and transactions**: the `registry_migrations` record is written only after `up()` succeeds; a failed migration has no record and is retried. There are no transactions and no down-migrations.
- **Imported helpers**: import an app helper only when the migration should follow the app's current logic; otherwise copy the logic in. The checksum covers only the migration file, and fresh databases replay every migration against current code.
- **Immutability**: applied migrations never change (CI `migration-immutability` job plus the runtime checksum). Fix forward with a new migration. The only exception is a deliberate, reviewed edit (e.g. a merged migration that crashes on fresh client databases):
  1. Open the PR with the `migration-edit-approved` label; the reviewer approves the edit explicitly.
  2. After it merges, in every environment where that migration was already applied (DEMO, PROD, each client), delete its record **after** the rollout to the new image has started: `db.registry_migrations.deleteOne({_id: "<version>"})`. `up` then re-runs it. Deleting it earlier lets a restarting old-image pod re-apply the old version and record the old checksum.

  The same record deletion covers a migration applied to DEMO from an unmerged branch and then edited.
- **Linting**: `versions/` is excluded from repo-wide ruff runs, so a ruff upgrade never rewrites an applied migration. Lint and format a new migration by path before committing it (never after merge): `uv run ruff check --fix <file>` and `uv run ruff format <file>`.
- **Out-of-order merges** are run, with a warning.
- **Running locally**: from the repo root, `MONGO_URI=mongodb://localhost:27017/jarvis uv run python -m registry_pkgs.migrations status` (or `up`). Other settings come from `.env`; the explicit URI overrides its in-network host. In compose, the `registry-migrations` service runs `up` automatically, but with the migrations baked into the published `registry:latest` image, so apply a migration you are writing with the `uv run` command.
- **Adding a member to `RegistryResourceType` requires a new migration that seeds its access roles.**

---

## Testing

### Organization
- Unit test paths mirror the source path where one exists (e.g. `registry/src/registry/services/a2a_agent_service.py` → `registry/tests/unit/services/test_a2a_agent_service.py`). Search for an existing test file before creating one.
- `tests/unit/` holds unit tests. Workspaces with API endpoints (`registry`, `auth-server`) also have `tests/integration/` for endpoint tests.
- Unit tests never reach real MongoDB, Redis, Weaviate, identity providers or AWS. Test routes in-process with FastAPI's `TestClient` (or httpx `AsyncClient`), and stub boto3 clients with botocore's `Stubber`.

### Running tests
- **Agents may run tests** without being asked. Suites print one summary line per workspace (e.g. `N passed in T s`); failures still print in full.
- **Scope**: run the narrowest scope while iterating, and the touched workspace's full suite before declaring done. If you touched `registry-pkgs`, run `uv run poe test-all` from the repo root, since every other Python workspace depends on it. If you expect many failures, use `--maxfail=N` first.
- **Commands**: from the workspace directory (e.g. `cd registry`), run `uv run poe test`, or `uv run pytest <path>` for one file or case. Options come from `pytest.ini`; don't pass other flags beyond `--maxfail`.

---

<!-- gitnexus:start -->
## GitNexus — Code Intelligence

The repo is indexed by GitNexus as **jarvis-registry** and served as a remote MCP server through jarvis-registry (setup in `DEVELOPMENT.md`). Use it when a call-graph or execution-flow view helps, such as tracing an unfamiliar flow (`gitnexus_query`, `gitnexus_context`, `gitnexus://repo/jarvis-registry/process/{name}`) or finding transitive callers before changing shared code (`gitnexus_impact`). It is optional.

- **The index reflects `main` as of the last CI rebuild.** It never sees your branch or working tree and can lag recent merges. `git grep` is the source of truth for call sites; treat GitNexus results as leads.
- **Don't use `gitnexus_detect_changes` or `gitnexus_rename`.** Both need a local checkout: `detect_changes` fails with `Not a git repository`, and `rename` returns `success` with zero edits.
<!-- gitnexus:end -->
