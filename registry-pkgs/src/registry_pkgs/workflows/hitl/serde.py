"""Serde for agno ``StepRequirement`` ↔ MongoDB-friendly dict.

Why
---
agno's ``StepRequirement`` is a Python dataclass with nested ``StepInput`` /
``StepOutput`` objects.  We persist these inside ``WorkflowRun.pending_requirements``
(a list of plain dicts) so the API layer can expose them to the frontend and so
``WorkflowRunner.continue_run`` can re-hydrate them before calling
``workflow.acontinue_run(step_requirements=...)``.

Schema versioning
-----------------
Every serialized requirement carries ``schema_version`` so we can detect / migrate
across agno upgrades that change the underlying dataclass shape.  When agno bumps
its types, increment ``CURRENT_SCHEMA_VERSION`` and add a branch in
``hydrate_requirement`` if the new layout is not back-compatible.

"""

from __future__ import annotations

import logging
from typing import Any

from agno.workflow.types import StepRequirement

logger = logging.getLogger(__name__)

CURRENT_SCHEMA_VERSION: int = 1


def serialize_requirement(requirement: StepRequirement) -> dict[str, Any]:
    """Convert an agno ``StepRequirement`` into a JSON/Mongo-friendly dict.

    Delegates to agno's own ``StepRequirement.to_dict()`` (which handles nested
    ``StepInput`` / ``StepOutput``) and adds our ``schema_version`` envelope.
    """
    data = requirement.to_dict()
    data["schema_version"] = CURRENT_SCHEMA_VERSION
    return data


def hydrate_requirement(data: dict[str, Any]) -> StepRequirement:
    """Reconstruct an agno ``StepRequirement`` from a previously serialized dict.

    Strips the ``schema_version`` envelope before delegating to
    ``StepRequirement.from_dict()``.
    """
    version = data.get("schema_version", CURRENT_SCHEMA_VERSION)
    if version != CURRENT_SCHEMA_VERSION:
        # Future hook for forward migrations.  For now we attempt best-effort
        # passthrough and log a warning so operators can spot incompatibilities.
        logger.warning(
            "requirement_serde: hydrating schema_version=%s with current=%s — verify compatibility",
            version,
            CURRENT_SCHEMA_VERSION,
        )
    payload = {k: v for k, v in data.items() if k != "schema_version"}
    return StepRequirement.from_dict(payload)
