from types import SimpleNamespace

import pytest
from beanie import PydanticObjectId

from registry_pkgs.models.enums import WorkflowNodeType, WorkflowRunStatus
from registry_pkgs.models.workflow import LoopConfig, WorkflowDefinition, WorkflowNode, WorkflowRun
from registry_pkgs.workflows import compiler


async def _executor(*args, **kwargs):
    return SimpleNamespace(content="ok")


def _step_node(name: str, executor_key: str = "tool") -> WorkflowNode:
    return WorkflowNode(name=name, node_type=WorkflowNodeType.STEP, executor_key=executor_key)


def _workflow_definition(nodes: list[WorkflowNode]) -> WorkflowDefinition:
    return WorkflowDefinition.model_construct(
        id=PydanticObjectId(),
        name="demo-workflow",
        nodes=nodes,
    )


def _workflow_run() -> WorkflowRun:
    return WorkflowRun.model_construct(
        id=PydanticObjectId(),
        workflow_definition_id=PydanticObjectId(),
        status=WorkflowRunStatus.RUNNING,
    )


@pytest.mark.unit
class TestWorkflowCompiler:
    def test_flatten_workflow_nodes_recurses_through_nested_children(self):
        definition_nodes = [
            WorkflowNode(
                name="parallel-root",
                node_type=WorkflowNodeType.PARALLEL,
                children=[
                    _step_node("first", "alpha"),
                    WorkflowNode(
                        name="loop-node",
                        node_type=WorkflowNodeType.LOOP,
                        loop_config=LoopConfig(max_iterations=2, end_condition_cel="done"),
                        children=[_step_node("second", "beta")],
                    ),
                ],
            )
        ]

        flat = compiler.flatten_workflow_nodes(definition_nodes)

        assert [node.name for node in flat] == ["parallel-root", "first", "loop-node", "second"]

    def test_compile_workflow_requires_db_client_and_db_name_together(self):
        definition = _workflow_definition([_step_node("first", "alpha")])
        run = _workflow_run()

        with pytest.raises(ValueError, match="db_client and db_name together"):
            compiler.compile_workflow(
                definition,
                run,
                executor_registry={"alpha": _executor},
                db_client=object(),
            )

        with pytest.raises(ValueError, match="db_client and db_name together"):
            compiler.compile_workflow(
                definition,
                run,
                executor_registry={"alpha": _executor},
                db_name="jarvis",
            )

    def test_compile_workflow_raises_for_missing_executor_key(self):
        definition = _workflow_definition([_step_node("first", "missing")])

        with pytest.raises(KeyError, match="executor_key 'missing' not found"):
            compiler.compile_workflow(definition, _workflow_run(), executor_registry={})

    def test_compile_workflow_builds_agno_steps_and_attaches_sync(self, monkeypatch: pytest.MonkeyPatch):
        captured = {}

        def fake_sync(**kwargs):
            captured.update(kwargs)
            return "db-sync"

        monkeypatch.setattr(compiler, "WorkflowRunSyncer", fake_sync)

        nodes = [
            _step_node("fetch", "fetcher"),
            WorkflowNode(
                name="parallel-root",
                node_type=WorkflowNodeType.PARALLEL,
                children=[_step_node("left", "left-tool"), _step_node("right", "right-tool")],
            ),
            WorkflowNode(
                name="condition-root",
                node_type=WorkflowNodeType.CONDITION,
                condition_cel="input == 'ok'",
                children=[_step_node("true-branch", "true-tool"), _step_node("false-branch", "false-tool")],
            ),
            WorkflowNode(
                name="loop-root",
                node_type=WorkflowNodeType.LOOP,
                loop_config=LoopConfig(max_iterations=5, end_condition_cel="session_state.done"),
                children=[_step_node("loop-body", "loop-tool")],
            ),
            WorkflowNode(
                name="router-root",
                node_type=WorkflowNodeType.ROUTER,
                condition_cel="step_choices[0]",
                children=[_step_node("route-a", "route-a-tool"), _step_node("route-b", "route-b-tool")],
            ),
        ]
        definition = _workflow_definition(nodes)
        run = _workflow_run()

        workflow = compiler.compile_workflow(
            definition,
            run,
            executor_registry={
                "fetcher": _executor,
                "left-tool": _executor,
                "right-tool": _executor,
                "true-tool": _executor,
                "false-tool": _executor,
                "loop-tool": _executor,
                "route-a-tool": _executor,
                "route-b-tool": _executor,
            },
            db_client="client",
            db_name="jarvis",
        )

        assert workflow.id == str(definition.id)
        assert workflow.name == definition.name
        assert workflow.db == "db-sync"
        assert [type(step).__name__ for step in workflow.steps] == ["Step", "Parallel", "Condition", "Loop", "Router"]

        first_step = workflow.steps[0]
        assert first_step.name == "fetch"
        assert first_step.executor is _executor
        assert first_step.max_retries == 0
        assert first_step.skip_on_failure is False

        parallel_step = workflow.steps[1]
        assert parallel_step.name == "parallel-root"
        assert [child.name for child in parallel_step.steps] == ["left", "right"]

        condition_step = workflow.steps[2]
        assert condition_step.name == "condition-root"
        assert condition_step.evaluator == "input == 'ok'"
        assert [child.name for child in condition_step.steps] == ["true-branch"]
        assert [child.name for child in condition_step.else_steps] == ["false-branch"]

        loop_step = workflow.steps[3]
        assert loop_step.name == "loop-root"
        assert loop_step.max_iterations == 5
        assert loop_step.end_condition == "session_state.done"
        assert [child.name for child in loop_step.steps] == ["loop-body"]

        router_step = workflow.steps[4]
        assert router_step.name == "router-root"
        assert router_step.selector == "step_choices[0]"
        assert [child.name for child in router_step.choices] == ["route-a", "route-b"]

        assert captured["workflow_run"] is run
        assert captured["db_client"] == "client"
        assert captured["db_name"] == "jarvis"
        assert set(captured["node_by_name"]) == {
            "fetch",
            "parallel-root",
            "left",
            "right",
            "condition-root",
            "true-branch",
            "false-branch",
            "loop-root",
            "loop-body",
            "router-root",
            "route-a",
            "route-b",
        }
