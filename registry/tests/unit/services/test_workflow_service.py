import re

import pytest

from registry.schemas.workflow_api_schemas import WorkflowCreateRequest, WorkflowNodeInput
from registry.services import workflow_service
from registry.services.workflow_service import WorkflowService


class _WorkflowFindQuery:
    def __init__(self, *, total: int = 0, workflows: list | None = None):
        self._total = total
        self._workflows = workflows or []

    async def count(self):
        return self._total

    def sort(self, *_args, **_kwargs):
        return self

    def skip(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    async def to_list(self):
        return self._workflows


@pytest.mark.asyncio
async def test_list_workflows_escapes_regex_query(monkeypatch: pytest.MonkeyPatch):
    captured_filters = []

    def fake_find(filters):
        captured_filters.append(filters)
        return _WorkflowFindQuery()

    monkeypatch.setattr(workflow_service.WorkflowDefinition, "find", fake_find)

    await WorkflowService().list_workflows(query="a.b[")

    search_pattern = captured_filters[0]["$or"][0]["name"]
    assert search_pattern == {"$regex": re.escape("a.b["), "$options": "i"}


@pytest.mark.asyncio
async def test_create_workflow_does_not_convert_unexpected_errors_to_value_error(monkeypatch: pytest.MonkeyPatch):
    class FailingWorkflowDefinition:
        def __init__(self, **_kwargs):
            pass

        async def insert(self):
            raise RuntimeError("database unavailable")

    monkeypatch.setattr(workflow_service, "WorkflowDefinition", FailingWorkflowDefinition)

    request = WorkflowCreateRequest(
        name="Demo workflow",
        nodes=[WorkflowNodeInput(name="Fetch", nodeType="step", executorKey="tool")],
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        await WorkflowService().create_workflow(request)
