from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId

from registry.services import workflow_control_service as wcs
from registry.services.workflow_control_service import WorkflowControlService
from registry_pkgs.models.enums import WorkflowRunStatus
from registry_pkgs.workflows.control import DirectiveQueue


@pytest.mark.asyncio
async def test_send_retry_child_inherits_workflow_version(monkeypatch: pytest.MonkeyPatch):
    """A retry child run must inherit the parent's workflow_version so the replay
    stays tied to the same definition version (deterministic replay)."""
    parent_run = SimpleNamespace(
        id=PydanticObjectId(),
        workflow_definition_id=PydanticObjectId(),
        workflow_version=2,
        status=WorkflowRunStatus.FAILED,
        initial_input={"user_text": "hi"},
        definition_snapshot={
            "name": "wf",
            "version": 2,
            "nodes": [{"id": "n1", "name": "s", "node_type": "step", "executor_key": "tool"}],
        },
    )

    captured: dict = {}

    class _FakeRun:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.id = PydanticObjectId()

        async def insert(self):
            return None

    monkeypatch.setattr(wcs, "WorkflowRun", _FakeRun)

    from registry_pkgs.models.workflow import WorkflowNode

    class _FakeDefinition:
        def __init__(self, **kwargs):
            self.nodes = [WorkflowNode(**n) for n in kwargs.get("nodes", [])]

    monkeypatch.setattr("registry_pkgs.models.workflow.WorkflowDefinition", _FakeDefinition)

    fake_node_run = MagicMock()
    fake_node_run.find.return_value.to_list = AsyncMock(return_value=[])
    monkeypatch.setattr(wcs, "NodeRun", fake_node_run)

    service = WorkflowControlService(
        directive_queue=DirectiveQueue(),
        runner_factory=lambda: SimpleNamespace(run=AsyncMock()),
    )
    service._load_run = AsyncMock(return_value=parent_run)

    await service.send_retry(
        str(parent_run.workflow_definition_id),
        str(parent_run.id),
        "n1",
        registry_token="tok",
        user_id="user-1",
    )
    # Let the fire-and-forget runner task settle to avoid pending-task warnings.
    await asyncio.sleep(0)

    assert captured["workflow_version"] == 2
    assert captured["parent_run_id"] == parent_run.id
    assert captured["definition_snapshot"] == parent_run.definition_snapshot
