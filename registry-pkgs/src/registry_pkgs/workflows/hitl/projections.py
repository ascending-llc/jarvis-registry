from __future__ import annotations

from pydantic import BaseModel

from registry_pkgs.models.enums import WorkflowDirective


class PendingDirectiveProjection(BaseModel):
    """Minimal projection — read pending_directive without loading the full WorkflowRun."""

    pending_directive: WorkflowDirective | None = None
