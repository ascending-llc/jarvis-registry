"""Flyway-style MongoDB data migrations: `python -m registry_pkgs.migrations {up|status|wait}`.

How to write a migration (idempotency patterns, testing): see `README.md` in this package.

Contract. Each `versions/m<NNNN>_<name>.py` defines a module-level `async def up(db: AsyncDatabase) -> None`.
A migration:
- writes with raw pymongo through `db` only, never Beanie `Document` classes (the runner never calls
  `init_beanie`, and the current model may reject or reshape old or Jarvis Chat documents);
- imports app helpers only when it should follow the app's current behaviour, and otherwise copies the logic
  in, because the checksum covers only the migration file;
- is idempotent: safe to re-run after a full or partial run, while old-code pods, new-code pods and Jarvis Chat
  write the same collections (the lock excludes other runners only);
- stays compatible with the previous release's code, because rollouts overlap old and new pods.
A record in `registry_migrations` is written only after `up()` returns. There are no down-migrations and no
transactions. Out-of-order migrations are run, with a warning.

Immutability. An applied migration must never change: fix forward with a new migration. CI fails a PR that
modifies, renames or deletes a merged migration, and the runner refuses to run when a recorded checksum
differs from the file. The only escape hatch is a deliberate, reviewed edit: open the PR with the
`migration-edit-approved` label, and after the rollout to the new image has started, delete the migration's
record in every environment where it was applied: `db.registry_migrations.deleteOne({_id: "<version>"})`.
Full procedure: AGENTS.md § Data Migrations.
"""
