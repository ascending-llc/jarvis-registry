"""Vector DB sync + verification for MCP Gateway Registry.

SYNC (default) rebuilds MCP servers and/or A2A agents from MongoDB into the vector DB; each entity is
deleted + reinserted, so it is safe to re-run and corrects stale/missing data.

VERIFY (--verify) probes a RUNNING registry:
  durable — stamp a "stuck" pending marker and watch the live leader-leased reconciler heal it
            (re-embed + clear); checks the leader-lease wiring and (with --check-grace) the grace
            window + backoff-eligibility. Observes only; writes nothing to Weaviate itself.
  crud    — drive the real repos and assert Weaviate create/read/update/delete + metadata patches.
            Runs on existing entities and restores them.
  orphan  — confirm the live app started the orphan sweeper (leader lease, hourly nextRunAt), then
            inject a synthetic orphan (vectors for a server_id with no Mongo entity), run one real
            sweep pass, and assert the orphan is deleted while a real server's vectors survive.

Reads settings from .env.

Usage:
    uv run python scripts/vector_sync.py --target mcp|a2a|all
    uv run python scripts/vector_sync.py --verify durable [--check-grace]
    uv run python scripts/vector_sync.py --verify crud
    uv run python scripts/vector_sync.py --verify orphan
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from beanie import PydanticObjectId

from registry.container import RegistryContainer
from registry.core.config import Settings
from registry.core.vector_backend import resolve_vector_backend_config
from registry.services.vector_orphan_sweeper import VectorOrphanSweeper
from registry_pkgs.database.mongodb import close_mongodb, init_mongodb
from registry_pkgs.database.redis_client import close_redis_client, create_redis_client
from registry_pkgs.models.a2a_agent import A2AAgent
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer
from registry_pkgs.vector.adapters.factory import VectorStoreFactory
from registry_pkgs.vector.client import create_database_client

# Mirror the reconciler's windows (vector_sync_reconciler.py) without importing app internals.
_LEASE_NAME = "vector-sync-reconciler"
_GRACE_SECONDS = 60
_POLL_SECONDS = 30


def _ok(name: str, passed: bool, detail: str = "") -> bool:
    print(f"  {'✓' if passed else '✗'} {name}" + (f"  — {detail}" if detail else ""))
    return passed


def _default_weaviate_url() -> str:
    return (
        os.getenv("WEAVIATE_URL")
        or f"http://{os.getenv('WEAVIATE_HOST', '127.0.0.1')}:{os.getenv('WEAVIATE_PORT', '8099')}"
    )


class Weaviate:
    """Tiny GraphQL reader for one entity's chunks in the active generation collection."""

    def __init__(self, url: str, api_key: str, generation: str) -> None:
        self._url = url.rstrip("/")
        self._gen = generation
        self._headers = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"

    def docs(self, base: str, id_field: str, id_val: str, fields: str) -> list[dict]:
        cls = f"{base}_{self._gen}"
        query = (
            f'{{Get{{{cls}(where:{{path:["{id_field}"],operator:Equal,valueText:"{id_val}"}})'
            f"{{{fields} _additional{{lastUpdateTimeUnix}}}}}}}}"
        )
        resp = httpx.post(f"{self._url}/v1/graphql", headers=self._headers, json={"query": query}, timeout=20)
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("errors"):
            raise RuntimeError(payload["errors"])
        return (((payload.get("data") or {}).get("Get") or {}).get(cls)) or []

    def count(self, base: str, id_field: str, id_val: str) -> int:
        return len(self.docs(base, id_field, id_val, id_field))

    def clock(self, base: str, id_field: str, id_val: str) -> int:
        rows = self.docs(base, id_field, id_val, id_field)
        return max((int(r["_additional"]["lastUpdateTimeUnix"]) for r in rows if r.get("_additional")), default=0)


async def _open_write_gate(container: RegistryContainer) -> bool:
    """Start the maintenance watcher and wait for the write gate to open (closed during a reindex)."""
    watcher = container.embedding_maintenance_watcher
    await watcher.start()
    for _ in range(50):  # ~5s
        if not watcher.is_active():
            return True
        await asyncio.sleep(0.1)
    return False


async def _active_generation(db) -> str | None:
    async for doc in db["model_gateway_selection"].find():
        if gen := doc.get("embeddingCollectionGeneration"):
            return str(gen)
    return None


# --- sync -----------------------------------------------------------------------------

# (model, repo attr, name getter, result's "failed" key) per target.
_TARGETS = {
    "mcp": (ExtendedMCPServer, "mcp_server_repo", lambda e: e.serverName, "failed_tools"),
    "a2a": (A2AAgent, "a2a_agent_repo", lambda e: e.card.name, "failed"),
}


async def _sync(container: RegistryContainer, targets: list[str]) -> int:
    failures = 0
    for name in targets:
        model, repo_attr, label, fail_key = _TARGETS[name]
        repo = getattr(container, repo_attr)
        ok = bad = 0
        print(f"\n== Syncing {name} ==")
        async for entity in model.find_all():
            try:
                res = await repo.sync_to_vector_db(entity, is_delete=True)
                if not res or res.get(fail_key) or res.get("error"):
                    bad += 1
                    print(f"  ✗ {label(entity)}: {res}")
                else:
                    ok += 1
                    print(f"  ✓ {label(entity)} (indexed={res.get('indexed_tools', res.get('indexed', 0))})")
            except Exception as exc:  # noqa: BLE001 — report and continue the batch
                bad += 1
                print(f"  ✗ {label(entity)}: {exc}")
        print(f"  {name}: synced={ok} failed={bad}")
        failures += bad
    return 1 if failures else 0


# --- verify: durable (observe the live reconciler) ------------------------------------


async def _verify_durable(db, wv_url: str, wv_key: str, *, check_grace: bool, timeout: int) -> bool:
    print("\n== VERIFY durable (reconciler) ==")
    servers = db["mcpservers"]

    lease = await db["registry_leader_leases"].find_one({"_id": _LEASE_NAME})
    if not _ok("leader-lease document exists", lease is not None, _LEASE_NAME):
        return False
    results = [_ok("lease is active", lease.get("status") == "active")]
    before_next = lease.get("nextRunAt")
    results.append(_ok("lease has nextRunAt", before_next is not None, str(before_next)))

    server = await servers.find_one({"config.enabled": True}, {"serverName": 1}) or await servers.find_one(
        {}, {"serverName": 1}
    )
    if server is None:
        return _ok("a server exists to test the reconciler", False)
    sid = server["_id"]
    orig = await servers.find_one({"_id": sid}, {"vectorSyncPendingAt": 1, "vectorSyncAttempts": 1}) or {}

    gen = await _active_generation(db)
    wv = Weaviate(wv_url, wv_key, gen) if gen else None
    base = ExtendedMCPServer.COLLECTION_NAME
    before_clock = wv.clock(base, "server_id", str(sid)) if wv else 0

    stuck = datetime.now(UTC) - timedelta(seconds=_GRACE_SECONDS + 60)
    await servers.update_one({"_id": sid}, {"$set": {"vectorSyncPendingAt": stuck, "vectorSyncAttempts": 0}})
    stored = (await servers.find_one({"_id": sid}, {"vectorSyncPendingAt": 1}))["vectorSyncPendingAt"]
    print(f"  stamped stuck marker on {server.get('serverName', sid)} ({sid})")

    cleared = acted = False
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(3)
        cur = await servers.find_one({"_id": sid}, {"vectorSyncPendingAt": 1, "vectorSyncAttempts": 1}) or {}
        pending, attempts = cur.get("vectorSyncPendingAt"), cur.get("vectorSyncAttempts")
        if pending is None:
            acted = cleared = True
            break
        if pending != stored or (attempts or 0) > 0:
            acted = True
            break
    results.append(_ok("live reconciler healed the stuck marker", acted, "cleared" if cleared else "re-queued"))

    if wv and cleared:
        results.append(
            _ok("chunks present in Weaviate after heal (real landing)", wv.count(base, "server_id", str(sid)) >= 1)
        )
        results.append(
            _ok(
                "Weaviate write clock advanced (really re-embedded)",
                wv.clock(base, "server_id", str(sid)) > before_clock,
            )
        )

    after_next = (await db["registry_leader_leases"].find_one({"_id": _LEASE_NAME}) or {}).get("nextRunAt")
    results.append(
        _ok(
            "nextRunAt advanced (loop is scheduling passes)",
            bool(before_next and after_next and after_next > before_next) or acted,
        )
    )

    if check_grace:
        wait = _POLL_SECONDS + 8
        for label, pend, attempts in (
            ("fresh (within grace)", datetime.now(UTC) - timedelta(seconds=10), 0),
            ("future (backed-off)", datetime.now(UTC) + timedelta(minutes=30), 5),
        ):
            await servers.update_one(
                {"_id": sid}, {"$set": {"vectorSyncPendingAt": pend, "vectorSyncAttempts": attempts}}
            )
            kept = (await servers.find_one({"_id": sid}, {"vectorSyncPendingAt": 1}))["vectorSyncPendingAt"]
            print(f"  waiting {wait}s to confirm a {label} marker is skipped...")
            await asyncio.sleep(wait)
            still = (await servers.find_one({"_id": sid}, {"vectorSyncPendingAt": 1}) or {}).get("vectorSyncPendingAt")
            results.append(_ok(f"{label} marker not acted on", still == kept))

    # Restore the marker to its original state.
    if orig.get("vectorSyncPendingAt") is None:
        await servers.update_one({"_id": sid}, {"$unset": {"vectorSyncPendingAt": "", "vectorSyncAttempts": ""}})
    else:
        await servers.update_one(
            {"_id": sid},
            {
                "$set": {
                    "vectorSyncPendingAt": orig["vectorSyncPendingAt"],
                    "vectorSyncAttempts": orig.get("vectorSyncAttempts", 0),
                }
            },
        )
    return all(results)


# --- verify: crud (drive the real repos) ----------------------------------------------


async def _verify_crud(container: RegistryContainer, db_client, wv_url: str, wv_key: str) -> bool:
    print("\n== VERIFY crud (Weaviate C/R/U/D + metadata) ==")
    wv = Weaviate(wv_url, wv_key, db_client.collection_generation)
    mcp_repo, a2a_repo = container.mcp_server_repo, container.a2a_agent_repo
    mcp_base, a2a_base = ExtendedMCPServer.COLLECTION_NAME, A2AAgent.COLLECTION_NAME

    server = await ExtendedMCPServer.find_one({"config.enabled": True}) or await ExtendedMCPServer.find_one({})
    agent = await A2AAgent.find_one({})
    if server is None or agent is None:
        return _ok("an enabled server and an agent exist", False)
    sid, aid = str(server.id), str(agent.id)
    results = []

    base_n = wv.count(mcp_base, "server_id", sid)
    base_clk = wv.clock(mcp_base, "server_id", sid)
    results.append(_ok("server chunks present (R)", base_n >= 1, f"{base_n}"))

    res = await mcp_repo.sync_to_vector_db(server, is_delete=True)
    results.append(
        _ok(
            "re-embed success, same count (U, idempotent)",
            bool(res) and not res.get("failed_tools") and wv.count(mcp_base, "server_id", sid) == base_n,
        )
    )
    results.append(_ok("write clock advanced (really re-written)", wv.clock(mcp_base, "server_id", sid) > base_clk))

    await mcp_repo.update_entity_metadata("server_id", sid, {"enabled": False})
    rows = wv.docs(mcp_base, "server_id", sid, "enabled")
    results.append(
        _ok(
            "metadata patch flips enabled in place, count unchanged",
            bool(rows) and all(r.get("enabled") is False for r in rows) and len(rows) == base_n,
        )
    )
    await mcp_repo.update_entity_metadata("server_id", sid, {"enabled": True})

    tools = sorted({r["tool_name"] for r in wv.docs(mcp_base, "server_id", sid, "tool_name") if r.get("tool_name")})
    if tools:
        await mcp_repo.update_tools_metadata(sid, [tools[0]], {"tool_enabled": False})
        after = wv.docs(mcp_base, "server_id", sid, "tool_name tool_enabled")
        off = all(r.get("tool_enabled") is False for r in after if r.get("tool_name") == tools[0])
        on = all(r.get("tool_enabled") is not False for r in after if r.get("tool_name") != tools[0])
        results.append(_ok("per-tool patch flips one tool only", off and on, tools[0]))
        await mcp_repo.update_tools_metadata(sid, [tools[0]], {"tool_enabled": True})

    removed = await mcp_repo.delete_by_server_id(sid, server.serverName)
    results.append(
        _ok("delete removes all chunks (D)", wv.count(mcp_base, "server_id", sid) == 0, f"removed={removed}")
    )
    results.append(_ok("delete of absent entity is a no-op", (await mcp_repo.delete_by_server_id(sid)) == 0))
    await mcp_repo.sync_to_vector_db(server, is_delete=False)
    results.append(_ok("re-create restores chunks (C)", wv.count(mcp_base, "server_id", sid) == base_n))

    a_n = wv.count(a2a_base, "agent_id", aid)
    results.append(_ok("agent chunks present (R, second repo)", a_n >= 1, f"{a_n}"))
    await a2a_repo.delete_by_agent_id(aid, agent.card.name)
    results.append(_ok("agent delete removes all chunks (D)", wv.count(a2a_base, "agent_id", aid) == 0))
    await a2a_repo.sync_to_vector_db(agent, is_delete=False)
    results.append(_ok("agent re-create restores chunks (C)", wv.count(a2a_base, "agent_id", aid) == a_n))
    return all(results)


async def _verify_orphan(db, container: RegistryContainer, db_client, wv_url: str, wv_key: str) -> bool:
    print("\n== VERIFY orphan (sweep deletes vectors with no Mongo entity) ==")
    results = []

    # 1. Liveness: the deployed image started the sweeper (ensure_leader_document) and scheduled it.
    lease = await db["registry_leader_leases"].find_one({"_id": "vector-orphan-sweep"})
    results.append(_ok("orphan-sweep leader-lease exists", lease is not None, "vector-orphan-sweep"))
    if lease is not None:
        results.append(
            _ok(
                "lease active with an hourly nextRunAt",
                lease.get("status") == "active" and lease.get("nextRunAt") is not None,
                str(lease.get("nextRunAt")),
            )
        )

    wv = Weaviate(wv_url, wv_key, db_client.collection_generation)
    mcp_repo = container.mcp_server_repo
    base = ExtendedMCPServer.COLLECTION_NAME

    # A real server is the control: its vectors must survive the sweep (it exists in Mongo).
    real = await ExtendedMCPServer.find_one({"config.enabled": True}) or await ExtendedMCPServer.find_one({})
    if real is None:
        return _ok("a real server exists as the sweep control", False)
    rid = str(real.id)
    real_before = wv.count(base, "server_id", rid)
    results.append(_ok("control server chunks present", real_before >= 1, f"{real_before}"))

    # 2. Inject an orphan: write vectors for a brand-new id that was never saved to Mongo.
    orphan = ExtendedMCPServer.model_construct(
        id=PydanticObjectId(),
        serverName=f"orphan-verify-{PydanticObjectId()}",
        config=real.config,
        author=real.author,
        path=f"/orphan-verify-{PydanticObjectId()}",
        registryDisabledTools=[],
    )
    oid = str(orphan.id)
    try:
        await mcp_repo.sync_to_vector_db(orphan, is_delete=False)
        results.append(_ok("orphan vectors injected (no Mongo entity)", wv.count(base, "server_id", oid) >= 1, oid))

        # 3. Run one real sweep pass in-process (the same logic the live hourly loop runs).
        sweeper = VectorOrphanSweeper(
            mcp_server_repo=container.mcp_server_repo,
            a2a_agent_repo=container.a2a_agent_repo,
            embedding_maintenance_watcher=container.embedding_maintenance_watcher,
        )
        interval = await sweeper._run_pass()
        results.append(_ok("sweep pass ran at the hourly interval", interval == timedelta(hours=1), str(interval)))
        results.append(_ok("orphan vectors deleted by the sweep", wv.count(base, "server_id", oid) == 0))
        results.append(
            _ok("control server's vectors survive the sweep", wv.count(base, "server_id", rid) == real_before)
        )
    finally:
        # Best-effort cleanup if the sweep did not remove it (e.g. an assertion failed earlier).
        if wv.count(base, "server_id", oid) > 0:
            await mcp_repo.delete_by_server_id(oid, "orphan-verify-cleanup")
    return all(results)


async def run() -> int:
    parser = argparse.ArgumentParser(description="Vector DB sync + verification.")
    parser.add_argument("--target", choices=["mcp", "a2a", "all"], help="entities to sync (required unless --verify)")
    parser.add_argument("--verify", choices=["durable", "crud", "orphan"], help="run verification instead of sync")
    parser.add_argument("--weaviate-url", default=_default_weaviate_url())
    parser.add_argument("--weaviate-api-key", default=os.getenv("WEAVIATE_API_KEY", ""))
    parser.add_argument("--timeout", type=int, default=180, help="durable: seconds to wait for a heal")
    parser.add_argument("--check-grace", action="store_true", help="durable: also check grace + backoff")
    args = parser.parse_args()
    if not args.verify and not args.target:
        parser.error("one of --target or --verify is required")

    env_path = Path(".env")
    settings = Settings(_env_file=str(env_path)) if env_path.exists() else Settings()

    db_client = redis_client = container = None
    try:
        await init_mongodb(settings.mongo_config)
        db = ExtendedMCPServer.get_pymongo_collection().database

        # durable is a pure Mongo+Weaviate probe of the running registry. sync and crud write here, so
        # they need the container (which wires the write gate) with the gate open.
        if args.verify != "durable":
            redis_client = create_redis_client(settings.redis_config)
            config = await resolve_vector_backend_config(settings)
            await asyncio.to_thread(VectorStoreFactory._create_embedding(config).embed_query, "preflight")
            db_client = create_database_client(config)
            container = RegistryContainer(settings=settings, db_client=db_client, redis_client=redis_client)
            if not await _open_write_gate(container):
                print("✗ Aborting: an embedding-model reindex is in progress.")
                return 1

        if args.verify == "durable":
            passed = await _verify_durable(
                db, args.weaviate_url, args.weaviate_api_key, check_grace=args.check_grace, timeout=args.timeout
            )
        elif args.verify == "crud":
            passed = await _verify_crud(container, db_client, args.weaviate_url, args.weaviate_api_key)
        elif args.verify == "orphan":
            passed = await _verify_orphan(db, container, db_client, args.weaviate_url, args.weaviate_api_key)
        else:
            targets = ["mcp", "a2a"] if args.target == "all" else [args.target]
            return await _sync(container, targets)

        print(f"\n{'✓ PASS' if passed else '✗ FAIL'}")
        return 0 if passed else 1
    finally:
        if container is not None:
            await container.shutdown()
        if redis_client is not None:
            close_redis_client(redis_client)
        if db_client is not None:
            db_client.close()
        await close_mongodb()


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
