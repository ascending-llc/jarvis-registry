# MCP Gateway Registry Scripts

This directory contains utility scripts for building, testing, and deploying MCP Gateway Registry services.

## Identity data cleanup

Run these scripts manually from the repository root with the existing `uv` environment. They use
`MongoSettings` (`MONGO_URI`, and `MONGODB_USERNAME` / `MONGODB_PASSWORD` when required), including the
normal `.env` settings. Set the intended database explicitly before running them. They require direct
database access; a browser session cookie is not a credential for these scripts.

These are reviewed operational tools, not automatic application migrations. MongoDB connections use raw
async PyMongo without Beanie initialization, so previewing a change does not initialize indexes or
collections. Neither script changes `users` indexes, email addresses, `emailVerified`, or `username`.

### Backfill an Entra issuer

```bash
uv run python scripts/backfill_user_openid_issuer.py --issuer "$ISSUER" --dry-run
uv run python scripts/backfill_user_openid_issuer.py --issuer "$ISSUER"
```

`--issuer` is required and must be an Entra tenant v2 issuer. Trailing slashes are removed. The script
rejects a database containing an existing OpenID issuer other than the supplied value, including an
explicit `null`. It fills only missing issuers on eligible OpenID users and separately unsets empty
`openidId` strings. A completed run is safe to repeat.

`--dry-run` prints the affected IDs and counts without writing. **Omitting `--dry-run` enables writes**
after confirmation; there is no `--apply` argument for this script. `--force` skips confirmation only.

### Purge explicitly selected users

```bash
uv run python scripts/purge_users.py --user-id "$OLD_USER_ID"
uv run python scripts/purge_users.py --user-id "$OLD_USER_ID" --apply
```

Repeat `--user-id` for multiple targets. Dry-run is the default; `--dry-run` makes it explicit and is
mutually exclusive with `--apply`. Actual deletion requires `--apply`, followed by confirmation unless
`--force` is supplied. The script prints a per-collection plan and actual deletion counts.

It checks shared-resource ACLs and unmapped references before any MongoDB or Weaviate write. The
unknown-reference scan discovers top-level fields from up to 500 documents per collection, then queries
the whole collection for those fields; it is not an exhaustive scan of nested references or rare fields.
ACLs referencing the user only through `grantedBy` are retained as audit records.

- `--skip-vector`: leave and report the targeted MCP Server vector documents; no Weaviate connection.
- `--allow-unmapped`: leave and report discovered unmapped references instead of blocking on them.
- `--force`: skip interactive confirmation; it does not bypass safety checks.

Neither escape option bypasses the foreign-principal ACL guard. A normal apply deletes MCP Server
vectors first, MongoDB dependencies and resource ACLs next, authored resources next, and users last.
Deletion can partially succeed; there is no transaction across MongoDB and Weaviate. Keep the target
users, resources, and ACLs quiescent for the whole preview/apply window. Back up MongoDB and arrange a
separate vector recovery path before deleting anything.

For email reuse involving an obsolete identity, follow the
[identity conflict resolution runbook](../docs/runbooks/identity-conflict-resolution.md).

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

## Diagnose Google group resolution

`diagnose_google_groups.py` makes the same Cloud Identity group lookup auth-server makes at Google
login, with the same service account and `GOOGLE_CUSTOMER_ID`, and prints Google's full error body when
it fails. For each group returned, it prints the scope group it grants and the group's
`owner: customers/C…`, which is the value for `GOOGLE_CUSTOMER_ID`. Pass a working and a failing account
together to compare them.

Inside the auth-server pod, which already has the settings in its environment:

```bash
kubectl exec -i <auth-server-pod> -- python - jason@example.com tommy@example.com \
    < scripts/diagnose_google_groups.py
```

Locally, from the repository root, with the settings in an env file copied from
`scripts/diagnose_google_groups.env.example` (`.env.*` files are gitignored):

```bash
uv run python scripts/diagnose_google_groups.py --env-file scripts/.env.google-groups \
    jason@example.com tommy@example.com
```

`--customer-id C0xxxxxxx` overrides `GOOGLE_CUSTOMER_ID`, e.g. to test an ID before adding it to the
secret. Exit codes: `0` every lookup succeeded, `1` any lookup failed, `2` configuration error (missing
or malformed service account key or customer ID).
