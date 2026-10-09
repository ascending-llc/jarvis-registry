"""The single write path for ``WorkflowRun`` mutations during execution and control.

Why this module exists: every ``WorkflowRun`` write used to be a Beanie
``save()``, which is a ``$set`` of *every* field filtered only on ``_id`` with
``upsert=True``. Two writers share the document — the executor (runner, syncer,
control wrapper) and the control plane (pause/resume/cancel API). Each ``save()``
of a stale in-memory copy silently overwrote the other's fields, so a user's
CANCEL could be lost, a COMPLETED run could regress to RUNNING, and a deleted run
could be resurrected by the upsert.

Every write here is instead a targeted, status-guarded ``update_one`` on the raw
pymongo collection — no Beanie ``save()``, no upsert:

* **Field ownership.** ``write()`` only accepts executor-owned fields and raises
  ``ValueError`` otherwise, so an ownership violation fails loudly in tests rather
  than silently clobbering a field another writer owns.

    | Owner         | Fields it may ``$set``                                            |
    |---------------|-------------------------------------------------------------------|
    | Executor      | status, finished_at, final_output, error_summary,                 |
    |               | definition_snapshot, pending_requirements, agno_run_id, paused_at |
    | Control plane | pending_directive; pending_requirements[] while AWAITING_APPROVAL; |
    |               | terminal fields of an AWAITING_APPROVAL run in reauth-fail         |
    | Executor ack  | pending_directive -> None, only via ``ack_directive`` (CAS)        |

* **Status guard (compare-and-set).** Each write carries ``from_statuses``; the
  filter ``status: {$in: from_statuses}`` makes a stale write match zero documents
  and be rejected instead of overwriting. A terminal write uses
  ``NON_TERMINAL_RUN_STATUSES`` so the first terminal outcome wins.

AS-1909 subclasses ``RunStateWriter`` to add ``lease_token`` to every filter, so
keep the constructor and method signatures stable and have each executor write
site use the writer instance it is handed rather than constructing its own.
"""

import logging
from collections.abc import Collection
from enum import Enum
from typing import Any

from beanie import PydanticObjectId
from pymongo.asynchronous.client_session import AsyncClientSession

from registry_pkgs.models.enums import WorkflowRunStateMachine, WorkflowRunStatus
from registry_pkgs.models.workflow import WorkflowRun

logger = logging.getLogger(__name__)

# Single source of truth for the terminal set (the state machine), re-exported here as the
# run-write vocabulary. Terminal outcomes are final: once a run reaches one, no further write
# should move it. NON_TERMINAL is the complement — including PENDING, which ACTIVE_STATUSES
# deliberately excludes — so terminal writes guarded on it give "first terminal outcome wins".
TERMINAL_RUN_STATUSES: frozenset[WorkflowRunStatus] = WorkflowRunStateMachine.TERMINAL_STATUSES
NON_TERMINAL_RUN_STATUSES: frozenset[WorkflowRunStatus] = frozenset(WorkflowRunStatus) - TERMINAL_RUN_STATUSES

# Fields the executor (runner, syncer, control wrapper, fallback markers) owns.
# ``pending_directive`` is NOT here: the control plane sets it, and the executor
# only ever clears it through ``ack_directive``'s compare-and-set.
EXECUTOR_FIELDS: frozenset[str] = frozenset(
    {
        "status",
        "finished_at",
        "final_output",
        "error_summary",
        "definition_snapshot",
        "pending_requirements",
        "agno_run_id",
        "paused_at",
    }
)


def _coerce(value: Any) -> Any:
    """Store enum members (StrEnum status, WorkflowDirective) as their raw value, as Beanie does."""
    return value.value if isinstance(value, Enum) else value


def _loggable(value: Any) -> Any:
    """A compact, safe rendering of a field value for logs.

    Keeps small scalars (status, directive, timestamps, ids) verbatim, but reduces large or
    potentially sensitive payloads (``final_output``, ``definition_snapshot``) to a shape marker
    so a per-write log never dumps workflow output or a whole definition snapshot.
    """
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return f"<dict:{len(value)} keys>"
    if isinstance(value, list):
        return value if not value else f"<list:{len(value)}>"
    if isinstance(value, str) and len(value) > 120:
        return value[:117] + "..."
    return value


def _summary(fields: dict[str, Any]) -> dict[str, Any]:
    return {key: _loggable(value) for key, value in fields.items()}


class RunStateWriter:
    """Issues targeted, status-guarded ``WorkflowRun`` writes for one ``run_id``."""

    def __init__(self, run_id: PydanticObjectId) -> None:
        self._run_id = run_id

    @staticmethod
    def _assert_executor_fields(fields: Collection[str]) -> None:
        illegal = set(fields) - EXECUTOR_FIELDS
        if illegal:
            raise ValueError(
                f"RunStateWriter may only set executor-owned fields; got non-executor field(s): {sorted(illegal)}"
            )

    async def write(
        self,
        set_fields: dict[str, Any],
        *,
        from_statuses: Collection[WorkflowRunStatus],
        unset: Collection[str] = (),
        session: AsyncClientSession | None = None,
    ) -> bool:
        """``$set`` ``set_fields`` (and ``$unset`` ``unset``) only while status is in ``from_statuses``.

        Returns True iff exactly one document matched and was updated. A False result
        means the run left the expected state (e.g. it became terminal) and the caller
        must not treat its in-memory copy as persisted.
        """
        self._assert_executor_fields(set(set_fields) | set(unset))
        update: dict[str, Any] = {}
        if set_fields:
            update["$set"] = {key: _coerce(value) for key, value in set_fields.items()}
        if unset:
            update["$unset"] = dict.fromkeys(unset, "")
        if not update:
            return False
        result = await WorkflowRun.get_pymongo_collection().update_one(
            {
                "_id": self._run_id,
                "status": {"$in": [_coerce(status) for status in from_statuses]},
            },
            update,
            session=session,
        )
        applied = result.matched_count == 1
        logger.info(
            "WorkflowRun %s write %s: set=%s unset=%s (guard status in %s)",
            self._run_id,
            "applied" if applied else "REJECTED (run left guarded state)",
            _summary(set_fields),
            list(unset),
            sorted(_coerce(status) for status in from_statuses),
        )
        return applied

    async def ack_directive(
        self,
        consumed: Any,
        set_fields: dict[str, Any] | None = None,
        *,
        from_statuses: Collection[WorkflowRunStatus],
    ) -> bool:
        """Clear ``pending_directive`` (plus optional executor ``set_fields``) with a CAS on ``consumed``.

        The ``pending_directive == consumed`` filter means the executor only clears the
        exact directive it processed; a newer directive written underneath it (e.g. PAUSE
        replaced by CANCEL) does not match, so this returns False instead of erasing it.
        """
        set_fields = set_fields or {}
        self._assert_executor_fields(set_fields)
        update_set = {key: _coerce(value) for key, value in set_fields.items()}
        update_set["pending_directive"] = None
        result = await WorkflowRun.get_pymongo_collection().update_one(
            {
                "_id": self._run_id,
                "status": {"$in": [_coerce(status) for status in from_statuses]},
                "pending_directive": _coerce(consumed),
            },
            {"$set": update_set},
        )
        applied = result.matched_count == 1
        logger.info(
            "WorkflowRun %s ack directive %s %s: set=%s (guard status in %s)",
            self._run_id,
            _coerce(consumed),
            "applied → pending_directive cleared" if applied else "REJECTED (directive/status changed)",
            _summary(set_fields),
            sorted(_coerce(status) for status in from_statuses),
        )
        return applied
