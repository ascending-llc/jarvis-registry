# MCP Gateway Registry Scripts

This directory contains utility scripts for building, testing, and deploying MCP Gateway Registry services.

## Federation Job Admin

Use the federation job admin helper to inspect or repair federation sync state.

Recommended command:

```bash
uv run federation-job-admin --help
```

Supported actions:
- `show <federation_id>`: inspect federation sync state plus recent jobs
- `list-active`: list all currently active federation jobs (`pending` / `syncing`)
- `fail-active <federation_id>`: mark the latest active job as failed and move the federation to `syncStatus=failed`
- `set-sync-state <federation_id>`: manually override federation `syncStatus` and `syncMessage`
- `retry-vector-sync <federation_id>`: rebuild Weaviate/vector index from the current Mongo state for that federation

Examples:

```bash
uv run federation-job-admin show federation-demo-id
uv run federation-job-admin list-active --limit 10
uv run federation-job-admin fail-active federation-demo-id --reason "manual recovery"
uv run federation-job-admin set-sync-state federation-demo-id --status failed --message "manual recovery"
uv run federation-job-admin retry-vector-sync federation-demo-id
```

Notes:
- `retry-vector-sync` does not rerun federation discovery or create a new federation job.
- `retry-vector-sync` reads the current MongoDB state and re-syncs existing MCP/A2A resources into the vector database.
- Running `retry-vector-sync` multiple times should converge to the same final vector state.
