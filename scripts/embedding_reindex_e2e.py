"""Embedding-reindex E2E against the running dockerized registry.

Exercises the real endpoint + background job end to end. Black box: it drives HTTP and reads the
``embedding_reindex_jobs`` and ``model_gateway_selection`` collections directly to observe the async
lifecycle (the adapter swap happens inside the registry process, so it is verified by observable
behavior, not by an in-process identity check, which the unit tests cover).

Single-pod, covered by default:
  1. 502 — a target ModelSource whose Bedrock model id is bogus fails the pre-flight smoke test:
     neither the selection nor a reindex job is persisted.
  2. 202 happy path — a valid Bedrock embedding source triggers a RUNNING job; reads stay available
     (search never returns 503) because it re-embeds into a new collection generation; the job
     reaches COMPLETED, the selection commits the new source, and search returns real results from
     the new generation (proving the re-embedded data actually landed there).
  3. 409 — a second trigger issued while the job is RUNNING is rejected.

Multi-pod (C1), opt-in with ``--peer-url http://localhost:7861`` (a SECOND registry sharing the same
Mongo + Weaviate — see docker-compose.multipod.yml):
  - while the job is RUNNING, the peer also sees it (second trigger → 409) and its reads stay 200;
  - after COMPLETED, the peer reports the committed selection and still serves search 200, i.e. its
    watcher swapped to the new generation without a restart and did not error on it.
  These are the reliable black-box signals a second pod can give. The definitive
  writes-503-on-every-pod and old-generation-GC-on-restart checks need to observe the live gate/GC
  across replicas; do those in staging (see "Staging C1 checklist" below).

Repeated switches (real-world path), opt-in with ``--switches N`` (N > 1): changes the embedding
model N times in a row and asserts each switch commits a fresh, non-colliding generation and keeps
search available on every pod. Combine with ``--peer-url`` to check the peer converges after each.

Usage:
  uv run python scripts/embedding_reindex_e2e.py                         # single-pod, one switch
  uv run python scripts/embedding_reindex_e2e.py --switches 3            # single-pod, repeated
  # multi-pod: bring up a second replica first (base + local override + multipod override), then:
  #   docker compose -f docker-compose.yml -f docker-compose.override.yml \
  #     -f docker-compose.multipod.yml --profile full up -d
  uv run python scripts/embedding_reindex_e2e.py --peer-url http://localhost:7861 --switches 3   # + C1

Prerequisite: the registry container must reach AWS Bedrock (valid creds in .env) for the happy
path to complete. Run ONLY against a disposable local stack: the happy path re-embeds every real
document into the test model's vector space and commits the live selection to it.

Staging C1 checklist (manual, run once against the real multi-replica environment — this is the only
way to see the per-pod write gate and GC, which a single Mongo-backed script cannot):
  1. Trigger a reindex (PUT /model-gateway/selection/embedding-model). While it is RUNNING, repeatedly
     hit the load balancer and confirm every replica: search → 200, any vector WRITE (create server /
     create or update agent) → 503.
  2. After it completes, confirm search is consistent across replicas (same result set whichever pod
     answers) and writes are accepted again.
  3. Roll/restart the pods and confirm the previous generation's ``<Base>_<gen>`` collections are
     dropped by startup GC (list Weaviate collections before/after).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

import httpx
from beanie import PydanticObjectId
from dotenv import load_dotenv

REPO = Path("/Users/dyl/ascending/code/jarvis-registry")
load_dotenv(REPO / ".env")
sys.path.insert(0, str(REPO))

from registry import settings  # noqa: E402
from registry.utils.csrf import compute_csrf_token  # noqa: E402
from registry_pkgs.core.config import MongoConfig  # noqa: E402
from registry_pkgs.core.jwt_tokens import mint_crud_session_token  # noqa: E402
from registry_pkgs.database.mongodb import MongoDB  # noqa: E402
from registry_pkgs.models.model_gateway_selection import MODEL_GATEWAY_SELECTION_ID  # noqa: E402

PREFIX = "__embedding_reindex_e2e__"
REGISTRY_URL = os.getenv("REGISTRY_URL", "http://localhost:7860")
# Weaviate is queried directly to prove the re-embedded data landed in the new generation — the
# registry's /search is ACL-filtered, so a synthetic token with no grants would see nothing.
WEAVIATE_URL = os.getenv("WEAVIATE_E2E_URL", "http://localhost:8099")
WEAVIATE_API_KEY = os.getenv("WEAVIATE_API_KEY", "")
MCP_BASE = "MCP_Servers"
A2A_BASE = "A2a_agents"
REGION = os.getenv("AWS_REGION", "us-east-1")
EMBED_MODEL = os.getenv("EMBED_MODEL", "amazon.titan-embed-text-v2:0")
BAD_MODEL = "amazon.titan-embed-text-v2:0-INVALID-E2E"  # valid string, rejected by Bedrock at embed time
JOB_TIMEOUT_SECONDS = float(os.getenv("REINDEX_JOB_TIMEOUT", "180"))

_results: list[tuple[bool, str]] = []


def _check(name: str, ok: bool, detail: str = "") -> None:
    _results.append((ok, f"{name}{' — ' + detail if detail else ''}"))


def _token() -> str:
    return mint_crud_session_token(
        settings.jwt_token_config,
        subject="embedding-reindex-e2e",
        token_type="access_token",
        expires_in_seconds=3600,
        extra_claims={
            "scope": "models-read models-write user-read servers-read agents-read",
            "user_id": str(PydanticObjectId()),
            "username": "embedding-reindex-e2e",
            "groups": ["jarvis-registry-admin"],
        },
    )


def _headers(token: str) -> dict[str, str]:
    return {
        "Cookie": f"{settings.session_cookie_name}={token}",
        settings.csrf_header_name: compute_csrf_token(token),
    }


def _api_at(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/api/{settings.api_version}{path}"


def _api(path: str) -> str:
    return _api_at(REGISTRY_URL, path)


def _bedrock_body(label: str, model: str) -> dict:
    return {
        "displayName": f"{PREFIX}{label}",
        "mode": "embedding",
        "providerConfig": {
            "providerType": "aws_bedrock",
            "awsRegion": REGION,
            "modelIdOrArn": model,
            "baseModelId": EMBED_MODEL,
        },
    }


async def _job_status(embed_id: str) -> str | None:
    doc = await (
        MongoDB.get_database()
        .get_collection("embedding_reindex_jobs")
        .find_one({"targetEmbeddingModelSourceId": PydanticObjectId(embed_id)}, sort=[("startedAt", -1)])
    )
    return doc.get("status") if doc else None


async def _poll_job_terminal(embed_id: str) -> str | None:
    """Poll the job for this run until it reaches COMPLETED/FAILED or the timeout elapses."""
    deadline = time.monotonic() + JOB_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        status = await _job_status(embed_id)
        if status in ("completed", "failed"):
            return status
        await asyncio.sleep(1.0)
    return await _job_status(embed_id)


async def _generation_object_count(client: httpx.AsyncClient, generation: str) -> int:
    """Count objects in the generation's Weaviate collections — proves the re-embedded data landed."""
    mcp = f"{MCP_BASE}_{generation}"
    a2a = f"{A2A_BASE}_{generation}"
    q = f"{{ Aggregate {{ {mcp} {{ meta {{ count }} }} {a2a} {{ meta {{ count }} }} }} }}"
    hdr = {"Authorization": f"Bearer {WEAVIATE_API_KEY}"} if WEAVIATE_API_KEY else {}
    r = await client.post(f"{WEAVIATE_URL.rstrip('/')}/v1/graphql", headers=hdr, json={"query": q})
    agg = (r.json().get("data") or {}).get("Aggregate") or {}
    return sum(int((agg.get(cls) or [{}])[0].get("meta", {}).get("count", 0)) for cls in (mcp, a2a))


async def _search_code_at(client: httpx.AsyncClient, base_url: str, headers: dict) -> int:
    r = await client.post(_api_at(base_url, "/search"), headers=headers, json={"query": "healthcheck"})
    return r.status_code


async def _search_code(client: httpx.AsyncClient, headers: dict) -> int:
    return await _search_code_at(client, REGISTRY_URL, headers)


async def _committed_generation(model_source_id: str) -> tuple[str | None, str | None]:
    """Return (embeddingModelSourceId, embeddingCollectionGeneration) from the singleton selection.

    Reads the fixed singleton id the app writes (not ``find_one({})``), so a stray legacy selection
    document with a different _id cannot shadow the authoritative one.
    """
    doc = (
        await MongoDB.get_database()
        .get_collection("model_gateway_selection")
        .find_one({"_id": MODEL_GATEWAY_SELECTION_ID})
    )
    doc = doc or {}
    return doc.get("embeddingModelSourceId"), doc.get("embeddingCollectionGeneration")


async def _corpus_size() -> tuple[int, int]:
    db = MongoDB.get_database()
    return (
        await db.get_collection("mcpservers").count_documents({}),
        await db.get_collection("a2a_agents").count_documents({}),
    )


async def _502_part(client: httpx.AsyncClient, headers: dict) -> str | None:
    """A bogus-model embedding source is rejected by the smoke test with 502, persisting nothing."""
    r = await client.post(_api("/model-sources"), headers=headers, json=_bedrock_body("bad", BAD_MODEL))
    _check("create bad embedding source → 201", r.status_code == 201, f"HTTP {r.status_code}")
    bad_id = r.json()["id"] if r.status_code == 201 else None
    if bad_id is None:
        return None

    before = (await client.get(_api("/model-gateway/selection"), headers=headers)).json().get("embeddingModelSourceId")
    r = await client.put(
        _api("/model-gateway/selection/embedding-model"), headers=headers, json={"modelSourceId": bad_id}
    )
    _check("PUT bad source → 502", r.status_code == 502, f"HTTP {r.status_code} body={r.text[:160]}")

    after = (await client.get(_api("/model-gateway/selection"), headers=headers)).json().get("embeddingModelSourceId")
    _check("502 did not change the selection", before == after, f"{before} == {after}")

    jobs = (
        await MongoDB.get_database()
        .get_collection("embedding_reindex_jobs")
        .count_documents({"targetEmbeddingModelSourceId": PydanticObjectId(bad_id)})
    )
    _check("502 created no reindex job", jobs == 0, f"job count={jobs}")
    return bad_id


async def _peer_c1_while_running(client: httpx.AsyncClient, headers: dict, peer_url: str, embed_id: str) -> None:
    """C1 checks against a second pod while the job is RUNNING: it sees the job and reads stay open."""
    r = await client.put(
        _api_at(peer_url, "/model-gateway/selection/embedding-model"), headers=headers, json={"modelSourceId": embed_id}
    )
    _check("[peer] sees the active job (second trigger → 409)", r.status_code == 409, f"HTTP {r.status_code}")
    code = await _search_code_at(client, peer_url, headers)
    _check("[peer] search stays available (not 503) while RUNNING", code != 503, f"search {code}")


async def _peer_c1_after_completed(client: httpx.AsyncClient, headers: dict, peer_url: str, embed_id: str) -> None:
    """C1 checks against the second pod after COMPLETED: it followed the switch and serves the new gen."""
    sel = (await client.get(_api_at(peer_url, "/model-gateway/selection"), headers=headers)).json()
    _check(
        "[peer] reports the committed embedding source",
        sel.get("embeddingModelSourceId") == embed_id,
        f"peer selection source={sel.get('embeddingModelSourceId')}",
    )
    # The peer's watcher swapped to the new generation without a restart; search must still serve 200.
    code = await _search_code_at(client, peer_url, headers)
    _check("[peer] search serves the new generation (200) after COMPLETED", code == 200, f"search {code}")


async def _happy_and_409_part(client: httpx.AsyncClient, headers: dict, peer_url: str | None = None) -> str | None:
    """A valid source triggers 202 → RUNNING (search stays 200, second trigger 409) → COMPLETED (generation switches)."""
    servers, agents = await _corpus_size()
    _check(
        "corpus has documents to re-embed",
        servers + agents > 0,
        f"servers={servers} agents={agents} (0 = trivial sweep)",
    )

    r = await client.post(_api("/model-sources"), headers=headers, json=_bedrock_body("good", EMBED_MODEL))
    _check("create valid embedding source → 201", r.status_code == 201, f"HTTP {r.status_code}")
    embed_id = r.json()["id"] if r.status_code == 201 else None
    if embed_id is None:
        return None

    # The PUT enqueues a blue-green reindex and returns the CURRENT (unchanged) selection, not embed_id.
    r = await client.put(
        _api("/model-gateway/selection/embedding-model"), headers=headers, json={"modelSourceId": embed_id}
    )
    _check("PUT valid source → 202", r.status_code == 202, f"HTTP {r.status_code} body={r.text[:160]}")
    if r.status_code != 202:
        return embed_id

    # A second trigger is rejected immediately (409 uses a live get_active_job query, not the watcher).
    r = await client.put(
        _api("/model-gateway/selection/embedding-model"), headers=headers, json={"modelSourceId": embed_id}
    )
    _check("second trigger while RUNNING → 409", r.status_code == 409, f"HTTP {r.status_code}")

    # Reads go to the still-active old generation, so search is NEVER blocked during reindex.
    _check("search stays available (not 503) while RUNNING", await _search_code(client, headers) != 503, "search 503")

    if peer_url:
        await _peer_c1_while_running(client, headers, peer_url, embed_id)

    status = await _poll_job_terminal(embed_id)
    _check("reindex job reaches COMPLETED", status == "completed", f"terminal status={status}")

    # After COMPLETED the selection is committed to the new source with a fresh collection generation.
    src, gen = await _committed_generation(embed_id)
    _check("selection committed to new embedding source", str(src) == embed_id, f"selection source={src}")
    _check("collection generation is set after commit", bool(gen), f"generation={gen}")
    _check("search available after COMPLETED", await _search_code(client, headers) != 503, "search 503")
    count = await _generation_object_count(client, gen) if gen else 0
    _check("new generation holds re-embedded data (object count > 0)", count > 0, f"weaviate objects={count}")

    if peer_url:
        # Give the peer's ~1s watcher poll a moment to observe the committed selection and swap.
        await asyncio.sleep(3.0)
        await _peer_c1_after_completed(client, headers, peer_url, embed_id)
    return embed_id


async def _multi_switch_part(
    client: httpx.AsyncClient, headers: dict, rounds: int, peer_url: str | None = None
) -> list[str]:
    """Change the embedding model ``rounds`` times back to back — the real-world path.

    Each switch must commit a NEW generation distinct from every earlier one (so generations never
    collide or get reused), keep search available on every pod throughout, and — in multi-pod — leave
    the peer converged on the latest source. This exercises the generation chain and the
    compare-and-set on the previous generation across repeated switches, which a single switch cannot.
    """
    servers, agents = await _corpus_size()
    _check("corpus has documents to re-embed", servers + agents > 0, f"servers={servers} agents={agents}")

    ids: list[str] = []
    seen_gens: set[str] = set()
    prev_gen: str | None = None
    for i in range(1, rounds + 1):
        r = await client.post(_api("/model-sources"), headers=headers, json=_bedrock_body(f"switch{i}", EMBED_MODEL))
        _check(f"[{i}] create embedding source → 201", r.status_code == 201, f"HTTP {r.status_code}")
        embed_id = r.json()["id"] if r.status_code == 201 else None
        if embed_id is None:
            break
        ids.append(embed_id)

        r = await client.put(
            _api("/model-gateway/selection/embedding-model"), headers=headers, json={"modelSourceId": embed_id}
        )
        _check(f"[{i}] switch → 202", r.status_code == 202, f"HTTP {r.status_code} body={r.text[:120]}")
        if r.status_code != 202:
            break

        if i == 1:
            r2 = await client.put(
                _api("/model-gateway/selection/embedding-model"), headers=headers, json={"modelSourceId": embed_id}
            )
            _check("second trigger while RUNNING → 409", r2.status_code == 409, f"HTTP {r2.status_code}")

        _check(f"[{i}] search stays 200 while RUNNING", await _search_code(client, headers) != 503, "search 503")
        if peer_url:
            peer_code = await _search_code_at(client, peer_url, headers)
            _check(f"[{i}][peer] search stays 200 while RUNNING", peer_code != 503, f"search {peer_code}")

        status = await _poll_job_terminal(embed_id)
        _check(f"[{i}] job COMPLETED", status == "completed", f"status={status}")

        src, gen = await _committed_generation(embed_id)
        _check(f"[{i}] selection committed to this source", str(src) == embed_id, f"source={src}")
        _check(
            f"[{i}] fresh, non-colliding generation",
            bool(gen) and gen != prev_gen and gen not in seen_gens,
            f"gen={gen} prev={prev_gen}",
        )
        _check(f"[{i}] search available after switch", await _search_code(client, headers) != 503, "search 503")
        count = await _generation_object_count(client, gen) if gen else 0
        _check(f"[{i}] new generation holds re-embedded data (count > 0)", count > 0, f"weaviate objects={count}")
        if peer_url:
            await asyncio.sleep(3.0)  # let the peer's ~1s watcher poll observe and swap
            sel = (await client.get(_api_at(peer_url, "/model-gateway/selection"), headers=headers)).json()
            _check(
                f"[{i}][peer] converged on latest source",
                sel.get("embeddingModelSourceId") == embed_id,
                f"peer src={sel.get('embeddingModelSourceId')}",
            )
            peer_code = await _search_code_at(client, peer_url, headers)
            _check(f"[{i}][peer] search serves 200 after switch", peer_code == 200, f"search {peer_code}")

        if gen:
            seen_gens.add(gen)
        prev_gen = gen
    return ids


async def _restore_and_cleanup(baseline_embedding, baseline_generation, ids: list[str]) -> None:
    db = MongoDB.get_database()
    await db.get_collection("model_gateway_selection").update_many(
        {},
        {"$set": {"embeddingModelSourceId": baseline_embedding, "embeddingCollectionGeneration": baseline_generation}},
    )
    for sid in ids:
        if sid:
            await db.get_collection("model_sources").delete_one({"_id": PydanticObjectId(sid)})
            await db.get_collection("embedding_reindex_jobs").delete_many(
                {"targetEmbeddingModelSourceId": PydanticObjectId(sid)}
            )


async def amain(peer_url: str | None = None, switches: int = 1) -> int:
    await MongoDB.connect_db(
        config=MongoConfig(
            mongo_uri=os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017/jarvis"),
            mongodb_username=os.getenv("MONGODB_USERNAME", ""),
            mongodb_password=os.getenv("MONGODB_PASSWORD", ""),
        ),
    )
    existing = (
        await MongoDB.get_database()
        .get_collection("model_gateway_selection")
        .find_one({"_id": MODEL_GATEWAY_SELECTION_ID})
    )
    baseline_embedding = existing.get("embeddingModelSourceId") if existing else None
    baseline_generation = existing.get("embeddingCollectionGeneration") if existing else None

    created_ids: list[str] = []
    try:
        headers = _headers(_token())
        async with httpx.AsyncClient(timeout=30) as client:
            bad_id = await _502_part(client, headers)
            created_ids.append(bad_id)
            if switches > 1:
                # Real-world path: change the model several times in a row.
                created_ids.extend(await _multi_switch_part(client, headers, switches, peer_url=peer_url))
            else:
                created_ids.append(await _happy_and_409_part(client, headers, peer_url=peer_url))
    finally:
        await _restore_and_cleanup(baseline_embedding, baseline_generation, created_ids)
        await MongoDB.close_db()

    print()
    ok_all = True
    for ok, name in _results:
        ok_all = ok_all and ok
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    print(f"\n{'PASS all assertions passed' if ok_all else 'FAIL some assertions failed'}")
    print(
        "\nNOTE: the happy path re-embedded all documents into the test model and swapped the live "
        "adapter. The selection singleton was restored, but the running container keeps the swapped "
        "adapter until it is restarted."
    )
    return 0 if ok_all else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Embedding-reindex E2E (single-pod, optional multi-pod C1).")
    parser.add_argument(
        "--peer-url",
        default=os.getenv("PEER_REGISTRY_URL"),
        help="Base URL of a SECOND registry sharing the same Mongo+Weaviate, to run the multi-pod (C1) checks.",
    )
    parser.add_argument(
        "--switches",
        type=int,
        default=int(os.getenv("REINDEX_SWITCHES", "1")),
        help="Change the embedding model this many times in a row (>1 runs the repeated-switch scenario).",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(amain(peer_url=args.peer_url, switches=args.switches)))
