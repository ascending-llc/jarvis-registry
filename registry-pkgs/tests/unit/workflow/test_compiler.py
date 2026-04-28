from types import SimpleNamespace

import pytest
from agno.workflow.step import OnError
from beanie import PydanticObjectId

from registry_pkgs.models.enums import WorkflowNodeType, WorkflowRunStatus
from registry_pkgs.models.workflow import LoopConfig, StepConfig, WorkflowDefinition, WorkflowNode, WorkflowRun
from registry_pkgs.workflows import compiler
from registry_pkgs.workflows.compiler import step_kwargs


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

        with pytest.raises(KeyError, match="executor key 'missing' not found"):
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


@pytest.mark.unit
class TestStepConfig:
    # ── StepConfig model validation ──────────────────────────────────────────

    def test_default_step_config_has_safe_production_values(self):
        cfg = StepConfig()
        assert cfg.max_retries == 0
        assert cfg.on_error == "fail"

    def test_step_config_accepts_valid_values(self):
        cfg = StepConfig(max_retries=3, on_error="skip")
        assert cfg.max_retries == 3
        assert cfg.on_error == "skip"

    def test_step_config_rejects_negative_max_retries(self):
        with pytest.raises(ValueError, match="max_retries must be >= 0"):
            StepConfig(max_retries=-1)

    def test_step_config_rejects_invalid_on_error(self):
        with pytest.raises(ValueError, match="on_error must be one of"):
            StepConfig(on_error="pause")

    def test_step_config_not_allowed_on_parallel_node(self):
        with pytest.raises(ValueError, match="must not define step_config"):
            WorkflowNode(
                name="par",
                node_type=WorkflowNodeType.PARALLEL,
                step_config=StepConfig(),
                children=[_step_node("a", "x"), _step_node("b", "y")],
            )

    def test_step_config_not_allowed_on_loop_node(self):
        with pytest.raises(ValueError, match="must not define step_config"):
            WorkflowNode(
                name="lp",
                node_type=WorkflowNodeType.LOOP,
                step_config=StepConfig(),
                loop_config=LoopConfig(max_iterations=2),
                children=[_step_node("body", "x")],
            )

    # ── _step_kwargs mapping ─────────────────────────────────────────────────

    def test_step_kwargs_defaults_when_no_config(self):
        kwargs = step_kwargs(None)
        assert kwargs["max_retries"] == 0
        assert kwargs["skip_on_failure"] is False
        assert kwargs["on_error"] == OnError.fail

    def test_step_kwargs_fail_config(self):
        kwargs = step_kwargs(StepConfig(max_retries=2, on_error="fail"))
        assert kwargs["max_retries"] == 2
        assert kwargs["skip_on_failure"] is False
        assert kwargs["on_error"] == OnError.fail

    def test_step_kwargs_skip_config(self):
        kwargs = step_kwargs(StepConfig(max_retries=1, on_error="skip"))
        assert kwargs["max_retries"] == 1
        assert kwargs["skip_on_failure"] is True
        assert kwargs["on_error"] == OnError.skip

    # ── compile_workflow honours StepConfig ──────────────────────────────────

    def test_compile_applies_step_config_to_agno_step(self):
        nodes = [
            WorkflowNode(
                name="flaky-step",
                node_type=WorkflowNodeType.STEP,
                executor_key="flaky-tool",
                step_config=StepConfig(max_retries=3, on_error="skip"),
            ),
            WorkflowNode(
                name="critical-step",
                node_type=WorkflowNodeType.STEP,
                executor_key="critical-tool",
                step_config=StepConfig(max_retries=0, on_error="fail"),
            ),
        ]
        definition = _workflow_definition(nodes)

        workflow = compiler.compile_workflow(
            definition,
            _workflow_run(),
            executor_registry={"flaky-tool": _executor, "critical-tool": _executor},
        )

        flaky = workflow.steps[0]
        assert flaky.max_retries == 3
        assert flaky.skip_on_failure is True

        critical = workflow.steps[1]
        assert critical.max_retries == 0
        assert critical.skip_on_failure is False

    def test_compile_uses_safe_defaults_when_step_config_absent(self):
        definition = _workflow_definition([_step_node("plain", "tool")])

        workflow = compiler.compile_workflow(
            definition,
            _workflow_run(),
            executor_registry={"tool": _executor},
        )

        step = workflow.steps[0]
        assert step.max_retries == 0
        assert step.skip_on_failure is False
