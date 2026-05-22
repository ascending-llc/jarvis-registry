import re

import pytest

from registry.schemas.workflow_api_schemas import (
    RouterChoiceInput,
    WorkflowCreateRequest,
    WorkflowNodeInput,
)
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
        canvas={"viewport": {"x": 0, "y": 0, "zoom": 1}},
        nodes=[WorkflowNodeInput(name="Fetch", nodeType="step", executorKey="tool")],
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        await WorkflowService().create_workflow(request)


def test_convert_step_node_preserves_executor_key():
    api_node = WorkflowNodeInput(name="fetch", nodeType="step", executorKey="tool-fetch")
    model = WorkflowService()._convert_api_node_to_model(api_node)
    assert model.name == "fetch"
    assert model.node_type == "step"
    assert model.executor_key == "tool-fetch"
    assert model.children == []
    assert model.true_steps == []
    assert model.false_steps == []
    assert model.choices == []


def test_convert_condition_node_maps_true_and_false_steps_recursively():
    """AS-1606 motivating shape: CONDITION with multi-step true & false branches."""
    api_node = WorkflowNodeInput(
        name="B",
        nodeType="condition",
        conditionCel="input.routeToTrue == true",
        trueSteps=[
            WorkflowNodeInput(name="C", nodeType="step", executorKey="tool-c"),
            WorkflowNodeInput(name="E", nodeType="step", executorKey="tool-e"),
            WorkflowNodeInput(name="G", nodeType="step", executorKey="tool-g"),
        ],
        falseSteps=[
            WorkflowNodeInput(name="D", nodeType="step", executorKey="tool-d"),
            WorkflowNodeInput(name="F", nodeType="step", executorKey="tool-f"),
            WorkflowNodeInput(name="H", nodeType="step", executorKey="tool-h"),
        ],
    )

    model = WorkflowService()._convert_api_node_to_model(api_node)

    assert model.node_type == "condition"
    assert model.condition_cel == "input.routeToTrue == true"
    assert [n.name for n in model.true_steps] == ["C", "E", "G"]
    assert [n.executor_key for n in model.true_steps] == ["tool-c", "tool-e", "tool-g"]
    assert [n.name for n in model.false_steps] == ["D", "F", "H"]
    assert model.children == []
    assert model.choices == []


def test_convert_router_node_maps_choices_with_multi_step_pipelines():
    api_node = WorkflowNodeInput(
        name="research-router",
        nodeType="router",
        conditionCel="input.strategy",
        choices=[
            RouterChoiceInput(
                name="tech",
                steps=[
                    WorkflowNodeInput(name="hn", nodeType="step", executorKey="hn-tool"),
                    WorkflowNodeInput(name="deep", nodeType="step", executorKey="deep-tool"),
                ],
            ),
            RouterChoiceInput(
                name="general",
                steps=[WorkflowNodeInput(name="web", nodeType="step", executorKey="web-tool")],
            ),
        ],
    )

    model = WorkflowService()._convert_api_node_to_model(api_node)

    assert model.node_type == "router"
    assert model.condition_cel == "input.strategy"
    assert model.children == []
    assert model.true_steps == []
    assert model.false_steps == []
    assert [c.name for c in model.choices] == ["tech", "general"]
    assert [s.name for s in model.choices[0].steps] == ["hn", "deep"]
    assert [s.executor_key for s in model.choices[0].steps] == ["hn-tool", "deep-tool"]
    assert [s.name for s in model.choices[1].steps] == ["web"]


def test_convert_condition_node_with_nested_router_recursively_converts():
    """Recursion must reach into router.choices[*].steps inside a condition branch."""
    api_node = WorkflowNodeInput(
        name="outer",
        nodeType="condition",
        conditionCel="x",
        trueSteps=[
            WorkflowNodeInput(
                name="inner-router",
                nodeType="router",
                conditionCel="step_choices[0]",
                choices=[
                    RouterChoiceInput(
                        name="a",
                        steps=[
                            WorkflowNodeInput(name="a1", nodeType="step", executorKey="tool-a1"),
                            WorkflowNodeInput(name="a2", nodeType="step", executorKey="tool-a2"),
                        ],
                    ),
                    RouterChoiceInput(
                        name="b",
                        steps=[WorkflowNodeInput(name="b1", nodeType="step", executorKey="tool-b1")],
                    ),
                ],
            ),
        ],
    )

    model = WorkflowService()._convert_api_node_to_model(api_node)

    assert model.node_type == "condition"
    assert len(model.true_steps) == 1
    inner = model.true_steps[0]
    assert inner.node_type == "router"
    assert [c.name for c in inner.choices] == ["a", "b"]
    assert [s.name for s in inner.choices[0].steps] == ["a1", "a2"]
    assert [s.name for s in inner.choices[1].steps] == ["b1"]


def test_convert_api_node_generates_id_when_missing():
    api_node = WorkflowNodeInput(name="x", nodeType="step", executorKey="tool")
    model = WorkflowService()._convert_api_node_to_model(api_node)
    assert isinstance(model.id, str) and len(model.id) > 0


def test_convert_api_node_preserves_explicit_id():
    api_node = WorkflowNodeInput(id="custom-id", name="x", nodeType="step", executorKey="tool")
    model = WorkflowService()._convert_api_node_to_model(api_node)
    assert model.id == "custom-id"
