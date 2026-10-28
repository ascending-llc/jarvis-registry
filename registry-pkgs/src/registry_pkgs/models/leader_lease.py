"""A singleton leader lease for background loops that must run on one pod at a cluster-wide interval.

Each loop owns one ``LeaderLease`` document and claims it through ``LeasedRepository``. Lease fencing
gives mutual exclusion; ``nextRunAt`` gives the interval: a pod claims only when the lease is free and
due (``leader_claimable_filter``), and on finishing stamps ``nextRunAt = now + interval``
(``release_leader``) so other pods don't re-run the pass before the interval elapses.

Per-entity leases (one per synced entity) also carry ``expireAt``, so a TTL index drops the lease of
an entity that is deleted by any path. Singleton loop leases never set it and never expire.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from beanie import Document
from pydantic import ConfigDict, Field
from pymongo import IndexModel
from pymongo.errors import DuplicateKeyError

from ..database.leased_job import (
    CAMEL_CASE_LEASE_FIELDS,
    Lease,
    LeasedDocumentMixin,
    LeasedRepository,
    LeaseSpec,
)


class LeaderLease(LeasedDocumentMixin, Document):
    """One named singleton lease; ``_id`` is the loop name, ``status`` is always ``active``."""

    id: str  # lease name  # type: ignore[assignment]
    status: Literal["active"] = "active"
    nextRunAt: datetime | None = None
    # TTL: set only on per-entity leases; the document is deleted once this passes.
    expireAt: datetime | None = None
    leaseOwner: str | None = None
    leaseToken: str | None = None  # fresh per claim; every lease-fenced write matches on it
    leaseExpiresAt: datetime | None = None
    heartbeatAt: datetime | None = None
    updatedAt: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "registry_leader_leases"
        keep_nulls = False
        use_state_management = True
        indexes = [IndexModel([("expireAt", 1)], expireAfterSeconds=0)]

    model_config = ConfigDict(populate_by_name=True)


LEADER_LEASE = LeaseSpec(
    document=LeaderLease,
    fields=CAMEL_CASE_LEASE_FIELDS,
    status_field="status",
    leased_statuses=frozenset({"active"}),
    duration=timedelta(minutes=2),
    heartbeat_interval=timedelta(seconds=30),
)


async def ensure_leader_document(name: str) -> None:
    """Insert the singleton lease if absent (idempotent), so ``claim`` never needs an upsert."""
    try:
        await LeaderLease(id=name).insert()
    except DuplicateKeyError:
        pass


def leader_claimable_filter(name: str, now: datetime) -> dict[str, Any]:
    """The claim filter for a leader lease: this document, currently *free* and *due*."""
    return {
        "_id": name,
        "$and": [
            {"$or": [{"leaseToken": None}, {"leaseExpiresAt": {"$lte": now}}]},  # free
            {"$or": [{"nextRunAt": None}, {"nextRunAt": {"$lte": now}}]},  # due
        ],
    }


async def release_leader(
    repository: LeasedRepository[LeaderLease],
    lease: Lease,
    *,
    interval: timedelta,
    expire_after: timedelta | None = None,
) -> None:
    """Release the lease and schedule the next pass ``interval`` from now (0 = resume a backlog at once).

    Called in a ``finally`` so a pass that raises still releases instead of holding until expiry.
    ``expire_after`` sets the TTL ``expireAt``; pass it only for leases that may be dropped when idle.
    """
    now = datetime.now(UTC)
    set_fields: dict[str, Any] = {"nextRunAt": now + interval}
    if expire_after is not None:
        set_fields["expireAt"] = now + expire_after
    await repository.transition(lease, set_fields, release=True)
