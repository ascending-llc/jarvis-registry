# Writing a migration

A migration is `versions/m<NNNN>_<name>.py` (next free number, `[a-z0-9_]` name) defining
`async def up(db: AsyncDatabase) -> None`. The runner (`python -m registry_pkgs.migrations up`) applies
every unrecorded migration in version order and records it in `registry_migrations` only after `up()`
returns. The contract and lifecycle rules are in `AGENTS.md` § Data Migrations; this file is how to meet them.

The one property every rule below serves: **`up()` must be safe to run again after a full or partial run**
(a pod killed mid-migration leaves partial writes and no record), while old-code pods, new-code pods and
Jarvis Chat write the same collections. The lock only excludes other runners, never app writes.

Use raw pymongo through `db`. Never use Beanie `Document` classes.

## Never block the event loop

The migration lock's heartbeat is an asyncio task on the same event loop as `up()`. Blocking the loop for longer
than the lock's TTL (60 s) lets the lease expire. Another pod's runner then takes the lock and starts the same
migration concurrently, and this runner exits 1 once it sees the lock is lost. So:

- Await every I/O call through the async `db`. No sync pymongo client, `requests`, or `time.sleep`.
- In a long Python loop over many documents (e.g. computing values for a `bulk_write`), process in batches and
  yield between them: `await asyncio.sleep(0)`.
- Stream large result sets with `async for doc in db["c"].find(...)` rather than one huge `to_list()`.

## Updates

- Put the precondition in the filter, so only unmigrated documents match. A re-run matches nothing, a
  partial run resumes, and values new-code pods already wrote are not overwritten:
  `await db["c"].update_many({"newField": {"$exists": False}}, {"$set": {"newField": value}})`
- Use idempotent operators only: `$set`, `$unset`, `$addToSet`, `$setOnInsert`, `$min`, `$max`, and `$pull`
  by value or condition. `$inc`, `$push`, `$mul` and `$pop` are not idempotent; if one is unavoidable, gate it
  on a marker field in the filter:
  `await db["c"].update_many({"m0002_done": {"$exists": False}}, {"$inc": {"n": 1}, "$set": {"m0002_done": True}})`
- For values derived in Python from other fields, compare-and-swap per document, batched with
  `bulk_write(ordered=False)`. A concurrent app edit then makes the write a no-op instead of being clobbered:
  `UpdateOne({"_id": doc["_id"], "src": doc["src"], "dst": {"$exists": False}}, {"$set": {"dst": f(doc["src"])}})`
- Renames: without the `new` condition, `$rename` overwrites a value a new-code pod already wrote. Documents
  that end up with both fields need an explicit merge rule:
  `await db["c"].update_many({"old": {"$exists": True}, "new": {"$exists": False}}, {"$rename": {"old": "new"}})`

## Inserts

- Upsert on a natural key with `$setOnInsert` (as `m0001_seed_access_roles` does), ideally backed by a unique
  index: `await db["c"].update_one({"key": k}, {"$setOnInsert": doc}, upsert=True)`
- Or `insert_one` with a deterministic `_id`, catching `DuplicateKeyError`:
  `await db["c"].insert_one({"_id": "seed-x", ...})` inside `try: ... except DuplicateKeyError: pass`
- Never check for existence and then `insert_one`: app pods and Chat can insert between the two.
- Never let `insert_one` generate an `ObjectId` for seed data: every retry adds a copy.

## Deletes

- Filter on fixed properties of the document's content, never on relative selections ("the N oldest",
  "all but the newest"), which select a different set on each run:
  `await db["c"].delete_many({"type": "legacy_x"})`
- When moving data, upsert into the target first and delete the source last:
  `await db["new"].update_one({"_id": d["_id"]}, {"$setOnInsert": d}, upsert=True)`, then
  `await db["old"].delete_one({"_id": d["_id"]})`
- Dedupe by a deterministic survivor rule, e.g. keep the minimum `_id` per key:
  `await db["c"].delete_many({"key": k, "_id": {"$ne": min_id}})`
- On shared collections, scope the filter so Chat's documents are never matched:
  `await db["accessroles"].delete_many({"resourceType": "workflow", "accessRoleId": "legacy_role"})`

## Indexes and collections

- `create_index` is a no-op only for an identical key, name and options. An existing index with the same key
  or name but a different definition raises `IndexOptionsConflict` (85) or `IndexKeySpecsConflict` (86), so
  check first: `if "field_1" not in await db["c"].index_information(): await db["c"].create_index("field")`
- An index that a Beanie model in `registry_pkgs/models/` or Chat's Mongoose schema also declares must match
  that declaration exactly (key, options, and the default name such as `field_1`). Otherwise the later
  `init_beanie` at service startup, or Chat's Mongoose auto-indexing, hits the conflict above: the same class
  of "index already exists" crash that rules out duplicate indexes on `Extended*` models.
- `drop_index` raises `IndexNotFound` (27) when the index is absent, so check first or catch that code:
  `except OperationFailure as e: if e.code != 27: raise`
- `drop_collection` is idempotent: `await db.drop_collection("legacy")`

## Logging

Log each write's result counts at INFO. The counts make the re-run check below observable, and they show
operators what a migration did:
`logger.info("backfilled newField: matched=%d modified=%d", r.matched_count, r.modified_count)`
(likewise `upserted_count` / `upserted_id`, `deleted_count`).

## Testing

- Unit tests (`registry-pkgs/tests/unit/migrations/versions/test_m<NNNN>_<name>.py`) use `AsyncMock`
  collections, like the rest of `registry-pkgs`. Mocks do not evaluate filters, so a test cannot show that a
  second run changes nothing. Assert instead the shape that makes the migration idempotent: filters exclude
  migrated documents, update documents use only idempotent operators, inserts are keyed upserts, and deletes
  use fixed filters after any copy.
- Before opening the PR, do a manual re-run check against the compose Mongo (published on `localhost:27017`):
  1. Insert a few documents in the pre-migration shape, so the first run's counts are non-zero. On an empty
     database both runs log zero and the check proves nothing.
  2. `MONGO_URI=mongodb://localhost:27017/jarvis uv run python -m registry_pkgs.migrations up` from the repo
     root. The explicit `MONGO_URI` is needed because `.env.example` uses the in-network host `mongodb`,
     which does not resolve from the host.
  3. `db.registry_migrations.deleteOne({_id: "<version>"})`
  4. The same `up` again. Its logged counts must show zero modified, upserted and deleted documents.

## Before committing

`versions/` is excluded from repo-wide ruff runs (so a ruff upgrade can never rewrite an applied migration
and change its checksum). Lint and format a new migration explicitly, before it is merged and never after:
`uv run ruff check --fix <file>` and `uv run ruff format <file>`. CI checks both on every added migration.
