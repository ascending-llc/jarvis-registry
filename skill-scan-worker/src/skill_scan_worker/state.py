"""Shared fenced finalize-or-requeue for a SCANNING job that must honour ``rerequested``.

``enqueue_skill_scan`` promises that whoever ends a SCANNING job honours a request that arrived
during it: every exit must, when ``rerequested`` is true, go to PENDING (attemptCount 0, rerequested false)
instead of its terminal state. Both the normal finish and the error finalizer need that two-step, fenced,
atomic-on-``rerequested`` write — so it lives here once instead of being copied into finish.py and main.py.
"""

from __future__ import annotations

from typing import Any

from pymongo.asynchronous.client_session import AsyncClientSession

from registry_pkgs.database.leased_job import Lease
from registry_pkgs.database.skill_scan_job_repository import skill_scan_job_repository


async def settle_or_requeue(
    lease: Lease,
    *,
    terminal_fields: dict[str, Any],
    requeue_fields: dict[str, Any],
    terminal_unset: list[str] | None = None,
    requeue_unset: list[str] | None = None,
    session: AsyncClientSession | None = None,
) -> str:
    """Apply ``terminal_fields`` when the job is not re-requested, else ``requeue_fields`` when it is.

    Each write is fenced on the lease and atomically on ``rerequested`` (``extra_filter``), so an enqueue
    racing the finish is never dropped. Returns ``"terminal"``, ``"requeued"``, or ``"lease_lost"`` (neither
    matched: the lease was lost or the job was deleted).
    """
    if await skill_scan_job_repository.transition(
        lease,
        terminal_fields,
        unset=terminal_unset,
        extra_filter={"rerequested": False},
        release=True,
        session=session,
    ):
        return "terminal"
    if await skill_scan_job_repository.transition(
        lease,
        requeue_fields,
        unset=requeue_unset,
        extra_filter={"rerequested": True},
        release=True,
        session=session,
    ):
        return "requeued"
    return "lease_lost"
