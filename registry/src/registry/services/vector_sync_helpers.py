"""Per-entity Weaviate sync primitives shared by the reindex executor, the immediate write path and
the reconciler, so "what counts as a failed sync" and "how a pending marker is cleared" are each
defined exactly once.

- ``is_failed_sync_result``: whether a repository sync result dict reports a failure.
- ``sync_server_vectors`` / ``sync_agent_vectors``: sync one entity, raising on failure.
- ``clear_vector_sync_marker``: compare-and-clear the ``vectorSyncPendingAt`` marker on success.
- ``VectorSyncPlan``: what a transactional write hands its route so the route can schedule the
  immediate sync *after* the commit (see ``server_service``/``a2a_agent_service``).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial

from beanie import Document, PydanticObjectId
from pymongo.asynchronous.client_session import AsyncClientSession

from registry_pkgs.database.leased_job import Lease, LeasedRepository, LeaseLostError, run_with_lease
from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.models import A2AAgent, ExtendedMCPServer, LeaderLease
from registry_pkgs.models.leader_lease import (
    LEADER_LEASE,
    ensure_leader_document,
    leader_claimable_filter,
    release_leader,
)
from registry_pkgs.vector.repositories.a2a_agent_repository import A2AAgentRepository
from registry_pkgs.vector.repositories.mcp_server_repository import MCPServerRepository

logger = logging.getLogger(__name__)


def is_failed_sync_result(result: dict) -> bool:
    """True when a sync result dict reports any failure.

    Rebuilds report ``failed`` (agents) or ``failed_tools`` (servers); a failed metadata patch sets
    ``error`` with ``failed == 0``, so ``error`` must be checked too or that patch would count as success.
    """
    return bool(result.get("failed") or result.get("failed_tools") or result.get("error"))


def _raise_if_failed(result: dict | None) -> None:
    if not result:
        raise RuntimeError("vector sync returned no result")
    if is_failed_sync_result(result):
        failed = result.get("failed") or result.get("failed_tools")
        raise RuntimeError(result.get("error") or f"{failed} vector updates failed")


async def sync_server_vectors(repo: MCPServerRepository, server: ExtendedMCPServer, *, is_delete: bool = False) -> None:
    """Sync one server into Weaviate; raise on failure so every caller fails the same way.

    ``is_delete=True`` replaces any existing vectors for the entity, so a changed chunk count drops
    the stale trailing chunks and stale ``enabled`` metadata is brought in line.
    """
    _raise_if_failed(await repo.sync_to_vector_db(server, is_delete=is_delete))


async def sync_agent_vectors(repo: A2AAgentRepository, agent: A2AAgent, *, is_delete: bool = False) -> None:
    """Sync one agent into Weaviate; raise on failure so every caller fails the same way."""
    _raise_if_failed(await repo.sync_to_vector_db(agent, is_delete=is_delete))


async def clear_vector_sync_marker(
    model: type[Document], doc_id: PydanticObjectId, marker: datetime, *, session: AsyncClientSession | None = None
) -> None:
    """Clear the pending marker only if it still holds ``marker``, by _id, without touching the entity.

    Compare-and-clear on the exact marker value: a newer write (which replaced the marker and reset
    the attempt counter) is never cleared by an older, slower sync. Uses the collection directly, not
    ``save()``, so neither ``updatedAt`` nor the content-hash hook fires.
    """
    await model.get_pymongo_collection().update_one(
        {"_id": doc_id, "vectorSyncPendingAt": marker},
        {"$unset": {"vectorSyncPendingAt": "", "vectorSyncAttempts": ""}},
        **({"session": session} if session is not None else {}),
    )


@dataclass(frozen=True)
class VectorSyncPlan:
    """How to sync one entity after its (possibly transactional) write commits.

    ``full_resync`` is the branch the immediate path takes: ``True`` re-embeds (content changed),
    ``False`` patches metadata only. ``marker`` is the value to compare-and-clear on success.
    """

    doc: ExtendedMCPServer | A2AAgent
    marker: datetime
    full_resync: bool
    # Tool deltas are valid only for this exact marker. An obsolete plan always rebuilds instead.
    tool_updates: tuple[tuple[tuple[str, ...], bool], ...] = ()


_repository: LeasedRepository[LeaderLease] = LeasedRepository(LEADER_LEASE)
type VectorModel = ExtendedMCPServer | A2AAgent
type VectorRepository = MCPServerRepository | A2AAgentRepository
# Idle per-entity leases are TTL-deleted after this, so leases of entities deleted by any path
# (federation, Jarvis Chat) don't accumulate. Far longer than any sync, so a held lease never expires.
_ENTITY_LEASE_TTL = timedelta(days=1)


class VectorSyncBusyError(RuntimeError):
    """Another worker owns this entity; its pending marker must remain eligible for reconciliation."""


def _entity_lease_name(model: type[VectorModel], doc_id: PydanticObjectId) -> str:
    return f"vector-entity:{model.get_collection_name()}:{doc_id}"


def _same_marker(left: datetime, right: datetime) -> bool:
    # BSON stores UTC milliseconds; plans originate as aware, microsecond-precision Python values.
    def milliseconds(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC, microsecond=value.microsecond // 1000 * 1000)

    return milliseconds(left) == milliseconds(right)


async def _mark_dirty(model: type[VectorModel], doc_id: PydanticObjectId) -> None:
    # Never upsert: deleted entities belong to the vector-delete lifecycle, not this reconciler.
    await model.get_pymongo_collection().update_one(
        {"_id": doc_id},
        {"$set": {"vectorSyncPendingAt": datetime.now(UTC), "vectorSyncAttempts": 0}},
    )


async def _drain(task: asyncio.Task[None]) -> None:
    """Don't detach a to_thread write even if shutdown cancels the parent more than once."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
        except Exception:
            break
    if not task.cancelled() and task.exception() is not None:
        logger.warning("Vector write failed while draining cancellation", exc_info=task.exception())


async def _write_vectors(
    repo: VectorRepository,
    doc: VectorModel,
    plan: VectorSyncPlan | None,
    runtime_identity: tuple[str, str] | None,
) -> None:
    full = plan is None or plan.full_resync or not _same_marker(doc.vectorSyncPendingAt, plan.marker)
    if runtime_identity is not None:
        await repo.delete_by_runtime_identity(*runtime_identity)
        full = True
    if full:
        if isinstance(doc, ExtendedMCPServer):
            await sync_server_vectors(repo, doc, is_delete=True)
        else:
            await sync_agent_vectors(repo, doc, is_delete=True)
        return

    # The repository methods patch metadata, or rebuild when the matching doc count drifts.
    reload = partial(type(doc).get, doc.id, ignore_cache=True)
    if plan.tool_updates:
        names = sorted({name for group, _ in plan.tool_updates for name in group})
        result = await repo.sync_tools_enabled(doc, names, reload=reload)
    else:
        result = await repo.sync_entity_metadata(doc, doc.mutable_metadata(), reload=reload)
    _raise_if_failed(result)


async def _execute(
    model: type[VectorModel],
    repo: VectorRepository,
    doc_id: PydanticObjectId,
    lease: Lease,
    plan: VectorSyncPlan | None,
    runtime_identity: tuple[str, str] | None,
) -> bool:
    doc = await model.get(doc_id, ignore_cache=True)
    if doc is None:
        return False
    if doc.vectorSyncPendingAt is None:
        if runtime_identity is None:
            return False  # Another worker already completed this request.
        # Repair-only federation work must also survive a crash after its destructive delete.
        await _mark_dirty(model, doc_id)
        doc = await model.get(doc_id, ignore_cache=True)
        if doc is None:
            return False
    await _repository.assert_owned(lease)
    operation = asyncio.create_task(_write_vectors(repo, doc, plan, runtime_identity))
    try:
        await asyncio.shield(operation)
        # Fence and clear in one transaction; takeover must conflict with this lease write.
        async with MongoDB.get_client().start_session() as session:
            async with await session.start_transaction():
                await _repository.transition_or_raise(lease, {}, session=session)
                await clear_vector_sync_marker(model, doc.id, doc.vectorSyncPendingAt, session=session)
    except asyncio.CancelledError:
        await _drain(operation)
        await _mark_dirty(model, doc_id)
        raise
    return True


async def sync_pending_vectors(
    model: type[VectorModel],
    repo: VectorRepository,
    doc_id: PydanticObjectId,
    *,
    owner: str,
    plan: VectorSyncPlan | None = None,
    runtime_identity: tuple[str, str] | None = None,
) -> bool:
    """Claim once, sync the latest committed entity, and clear only under the same ownership.

    Contention does not back off the document: another pass can retry its still-pending marker.
    A stale plan is promoted to full sync, including stale tool deltas. Federation runtime cleanup
    runs inside this same lease, never before acquiring it. ``owner`` is the pod's lease owner.
    """
    name = _entity_lease_name(model, doc_id)
    await ensure_leader_document(name)
    now = datetime.now(UTC)
    claimed = await _repository.claim(
        owner=owner,
        claimable_filter=leader_claimable_filter(name, now),
        set_fields={"expireAt": now + _ENTITY_LEASE_TTL},
    )
    if claimed is None:
        raise VectorSyncBusyError(f"Vector sync already running for {doc_id}")
    _, lease = claimed
    try:
        return await run_with_lease(_repository, lease, _execute(model, repo, doc_id, lease, plan, runtime_identity))
    except LeaseLostError:
        # run_with_lease has drained _execute before returning. A new marker invalidates a newer
        # worker's clear even when that worker completed during the old write's drain.
        await _mark_dirty(model, doc_id)
        raise
    finally:
        await release_leader(_repository, lease, interval=timedelta(0), expire_after=_ENTITY_LEASE_TTL)
