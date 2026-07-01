from types import SimpleNamespace

import pytest
from agno.workflow import StepInput
from agno.workflow.step import OnError
from beanie import PydanticObjectId

from registry_pkgs.models.enums import WorkflowNodeType, WorkflowRunStatus
from registry_pkgs.models.workflow import (
    LoopConfig,
    RouterChoice,
    StepConfig,
    WorkflowDefinition,
    WorkflowNode,
    WorkflowRun,
)
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
                true_steps=[_step_node("true-branch", "true-tool")],
                false_steps=[_step_node("false-branch", "false-tool")],
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
                choices=[
                    RouterChoice(name="route-a", steps=[_step_node("route-a", "route-a-tool")]),
                    RouterChoice(name="route-b", steps=[_step_node("route-b", "route-b-tool")]),
                ],
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
        assert first_step.executor is not _executor
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

        assert [type(c).__name__ for c in router_step.choices] == ["Steps", "Steps"]
        assert [child.name for child in router_step.choices] == ["route-a", "route-b"]
        assert [s.name for s in router_step.choices[0].steps] == ["route-a"]
        assert [s.name for s in router_step.choices[1].steps] == ["route-b"]

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

    @pytest.mark.asyncio
    async def test_step_executor_captures_input_snapshot_in_session_state(self):
        node = _step_node("fetch", "fetcher")
        definition = _workflow_definition([node])
        workflow = compiler.compile_workflow(
            definition,
            _workflow_run(),
            executor_registry={"fetcher": _executor},
        )
        session_state = {}

        await workflow.steps[0].executor(
            StepInput(input={"prompt": "hello"}, previous_step_content="prior"),
            session_state,
        )

        snapshots = session_state[compiler.NODE_INPUT_SNAPSHOTS_KEY]
        assert snapshots[node.id]["input"] == {"prompt": "hello"}
        assert snapshots[node.id]["previous_step_content"] == "prior"

    @pytest.mark.asyncio
    async def test_step_executor_parses_json_string_input_into_dict(self):
        """When the user triggers a workflow with a JSON payload, extract_user_text falls
        back to json.dumps(initial_input), so StepInput.input arrives as a JSON-encoded
        string. The snapshot must store the parsed object, not the escaped string."""
        node = _step_node("fetch", "fetcher")
        definition = _workflow_definition([node])
        workflow = compiler.compile_workflow(
            definition,
            _workflow_run(),
            executor_registry={"fetcher": _executor},
        )
        session_state = {}
        raw_payload = '{"method": "message/send", "params": {"text": "hi"}}'

        await workflow.steps[0].executor(
            StepInput(input=raw_payload),
            session_state,
        )

        snapshots = session_state[compiler.NODE_INPUT_SNAPSHOTS_KEY]
        assert snapshots[node.id]["input"] == {"method": "message/send", "params": {"text": "hi"}}

    @pytest.mark.asyncio
    async def test_step_executor_preserves_plain_text_input_as_string(self):
        """Plain natural-language input (LLM prompts, prose) is not valid JSON and must
        pass through unchanged."""
        node = _step_node("fetch", "fetcher")
        definition = _workflow_definition([node])
        workflow = compiler.compile_workflow(
            definition,
            _workflow_run(),
            executor_registry={"fetcher": _executor},
        )
        session_state = {}

        await workflow.steps[0].executor(
            StepInput(input="3+4 and weather in London"),
            session_state,
        )

        snapshots = session_state[compiler.NODE_INPUT_SNAPSHOTS_KEY]
        assert snapshots[node.id]["input"] == "3+4 and weather in London"

    @pytest.mark.asyncio
    async def test_step_executor_preserves_json_scalar_input_as_string(self):
        """A plain-text prompt that happens to be a valid JSON scalar (e.g. "123", "true")
        must not be silently retyped into an int/bool — only object/array strings are parsed."""
        node = _step_node("fetch", "fetcher")
        definition = _workflow_definition([node])
        workflow = compiler.compile_workflow(
            definition,
            _workflow_run(),
            executor_registry={"fetcher": _executor},
        )

        for raw_input in ("123", "true", '"hello"'):
            session_state = {}
            await workflow.steps[0].executor(
                StepInput(input=raw_input),
                session_state,
            )
            snapshots = session_state[compiler.NODE_INPUT_SNAPSHOTS_KEY]
            assert snapshots[node.id]["input"] == raw_input

    @pytest.mark.asyncio
    async def test_step_executor_parses_json_array_string_input_into_list(self):
        """A JSON array string (starting with '[') must be parsed into a list, not stored
        as an escaped string — the same treatment as object-shaped JSON inputs."""
        node = _step_node("fetch", "fetcher")
        definition = _workflow_definition([node])
        workflow = compiler.compile_workflow(
            definition,
            _workflow_run(),
            executor_registry={"fetcher": _executor},
        )
        session_state = {}

        await workflow.steps[0].executor(
            StepInput(input='[{"id": 1}, {"id": 2}]'),
            session_state,
        )

        snapshots = session_state[compiler.NODE_INPUT_SNAPSHOTS_KEY]
        assert snapshots[node.id]["input"] == [{"id": 1}, {"id": 2}]


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


@pytest.mark.unit
class TestConditionCompilation:
    def test_condition_with_single_true_step_compiles(self):
        node = WorkflowNode(
            name="cond",
            node_type=WorkflowNodeType.CONDITION,
            condition_cel="x > 0",
            true_steps=[_step_node("t1", "tool-t1")],
        )
        workflow = compiler.compile_workflow(
            _workflow_definition([node]),
            _workflow_run(),
            executor_registry={"tool-t1": _executor},
        )
        cond = workflow.steps[0]
        assert type(cond).__name__ == "Condition"
        assert [s.name for s in cond.steps] == ["t1"]
        assert cond.else_steps is None

    def test_condition_with_multiple_true_steps_compiles_sequentially(self):
        node = WorkflowNode(
            name="cond",
            node_type=WorkflowNodeType.CONDITION,
            condition_cel="x > 0",
            true_steps=[
                _step_node("C", "tool-c"),
                _step_node("E", "tool-e"),
                _step_node("G", "tool-g"),
            ],
        )
        workflow = compiler.compile_workflow(
            _workflow_definition([node]),
            _workflow_run(),
            executor_registry={"tool-c": _executor, "tool-e": _executor, "tool-g": _executor},
        )
        cond = workflow.steps[0]
        assert [s.name for s in cond.steps] == ["C", "E", "G"]

    def test_condition_with_multi_step_true_and_false_branches_compiles(self):
        node = WorkflowNode(
            name="cond",
            node_type=WorkflowNodeType.CONDITION,
            condition_cel="x > 0",
            true_steps=[_step_node("C", "tool-c"), _step_node("E", "tool-e")],
            false_steps=[_step_node("D", "tool-d"), _step_node("F", "tool-f")],
        )
        workflow = compiler.compile_workflow(
            _workflow_definition([node]),
            _workflow_run(),
            executor_registry={
                "tool-c": _executor,
                "tool-e": _executor,
                "tool-d": _executor,
                "tool-f": _executor,
            },
        )
        cond = workflow.steps[0]
        assert [s.name for s in cond.steps] == ["C", "E"]
        assert [s.name for s in cond.else_steps] == ["D", "F"]

    def test_condition_without_false_steps_has_none_else(self):
        node = WorkflowNode(
            name="cond",
            node_type=WorkflowNodeType.CONDITION,
            condition_cel="x > 0",
            true_steps=[_step_node("C", "tool-c")],
        )
        workflow = compiler.compile_workflow(
            _workflow_definition([node]),
            _workflow_run(),
            executor_registry={"tool-c": _executor},
        )
        assert workflow.steps[0].else_steps is None

    def test_motivating_example_tree_shaped_workflow(self):
        """A → B[CONDITION] true: C→E→G  false: D→F→H — original ticket scenario."""
        A = _step_node("A", "tool-a")
        B = WorkflowNode(
            name="B",
            node_type=WorkflowNodeType.CONDITION,
            condition_cel="input == 'yes'",
            true_steps=[
                _step_node("C", "tool-c"),
                _step_node("E", "tool-e"),
                _step_node("G", "tool-g"),
            ],
            false_steps=[
                _step_node("D", "tool-d"),
                _step_node("F", "tool-f"),
                _step_node("H", "tool-h"),
            ],
        )
        definition = _workflow_definition([A, B])

        flat_names = [n.name for n in compiler.flatten_workflow_nodes(definition.nodes)]
        assert flat_names == ["A", "B", "C", "E", "G", "D", "F", "H"]

        workflow = compiler.compile_workflow(
            definition,
            _workflow_run(),
            executor_registry={
                "tool-a": _executor,
                "tool-c": _executor,
                "tool-e": _executor,
                "tool-g": _executor,
                "tool-d": _executor,
                "tool-f": _executor,
                "tool-h": _executor,
            },
        )
        assert [type(s).__name__ for s in workflow.steps] == ["Step", "Condition"]
        cond = workflow.steps[1]
        assert [s.name for s in cond.steps] == ["C", "E", "G"]
        assert [s.name for s in cond.else_steps] == ["D", "F", "H"]


@pytest.mark.unit
class TestRouterCompilation:
    def test_router_single_step_choice_is_wrapped_in_steps(self):
        """Single-step choices are still wrapped in agno Steps so the selector can match
        choice.name regardless of inner step count. This prevents the selector contract
        from silently changing when a choice grows from one step to many."""
        node = WorkflowNode(
            name="rt",
            node_type=WorkflowNodeType.ROUTER,
            condition_cel="step_choices[0]",
            choices=[
                RouterChoice(name="a", steps=[_step_node("a-step", "tool-a")]),
                RouterChoice(name="b", steps=[_step_node("b-step", "tool-b")]),
            ],
        )
        workflow = compiler.compile_workflow(
            _workflow_definition([node]),
            _workflow_run(),
            executor_registry={"tool-a": _executor, "tool-b": _executor},
        )
        router = workflow.steps[0]
        assert type(router).__name__ == "Router"
        assert [type(c).__name__ for c in router.choices] == ["Steps", "Steps"]
        # Choice container name == RouterChoice.name (NOT the inner step's name)
        assert [c.name for c in router.choices] == ["a", "b"]
        assert [s.name for s in router.choices[0].steps] == ["a-step"]
        assert [s.name for s in router.choices[1].steps] == ["b-step"]

    def test_router_multi_step_choice_wraps_in_steps_container(self):
        node = WorkflowNode(
            name="rt",
            node_type=WorkflowNodeType.ROUTER,
            condition_cel="step_choices[0]",
            choices=[
                RouterChoice(
                    name="tech-path",
                    steps=[_step_node("hn", "tool-hn"), _step_node("deep", "tool-deep")],
                ),
                RouterChoice(name="general", steps=[_step_node("web", "tool-web")]),
            ],
        )
        workflow = compiler.compile_workflow(
            _workflow_definition([node]),
            _workflow_run(),
            executor_registry={"tool-hn": _executor, "tool-deep": _executor, "tool-web": _executor},
        )
        router = workflow.steps[0]
        # All choices wrapped in Steps regardless of inner-step count (uniform contract).
        assert [type(c).__name__ for c in router.choices] == ["Steps", "Steps"]
        tech_choice = router.choices[0]
        assert tech_choice.name == "tech-path"
        assert [s.name for s in tech_choice.steps] == ["hn", "deep"]
        general_choice = router.choices[1]
        assert general_choice.name == "general"
        assert [s.name for s in general_choice.steps] == ["web"]


@pytest.mark.unit
class TestFlattenWorkflowNodes:
    def test_flatten_includes_condition_true_and_false_steps(self):
        nodes = [
            WorkflowNode(
                name="cond",
                node_type=WorkflowNodeType.CONDITION,
                condition_cel="x",
                true_steps=[_step_node("t1", "tool-t1"), _step_node("t2", "tool-t2")],
                false_steps=[_step_node("f1", "tool-f1")],
            )
        ]
        flat = [n.name for n in compiler.flatten_workflow_nodes(nodes)]
        assert flat == ["cond", "t1", "t2", "f1"]

    def test_flatten_includes_router_choice_steps(self):
        nodes = [
            WorkflowNode(
                name="rt",
                node_type=WorkflowNodeType.ROUTER,
                condition_cel="step_choices[0]",
                choices=[
                    RouterChoice(name="a", steps=[_step_node("a1", "tool-a1"), _step_node("a2", "tool-a2")]),
                    RouterChoice(name="b", steps=[_step_node("b1", "tool-b1")]),
                ],
            )
        ]
        flat = [n.name for n in compiler.flatten_workflow_nodes(nodes)]
        assert flat == ["rt", "a1", "a2", "b1"]


@pytest.mark.unit
class TestConditionNodeValidation:
    def test_condition_with_single_true_step_is_valid(self):
        node = WorkflowNode(
            name="c",
            node_type=WorkflowNodeType.CONDITION,
            condition_cel="x",
            true_steps=[_step_node("t", "tool")],
        )
        assert node.true_steps[0].name == "t"

    def test_condition_with_multiple_true_steps_is_valid(self):
        node = WorkflowNode(
            name="c",
            node_type=WorkflowNodeType.CONDITION,
            condition_cel="x",
            true_steps=[_step_node("a", "tool-a"), _step_node("b", "tool-b")],
        )
        assert len(node.true_steps) == 2

    def test_condition_without_true_steps_raises(self):
        with pytest.raises(ValueError, match="requires at least one true_steps entry"):
            WorkflowNode(
                name="c",
                node_type=WorkflowNodeType.CONDITION,
                condition_cel="x",
            )

    def test_condition_without_condition_cel_raises(self):
        with pytest.raises(ValueError, match="condition node requires condition_cel"):
            WorkflowNode(
                name="c",
                node_type=WorkflowNodeType.CONDITION,
                true_steps=[_step_node("t", "tool")],
            )

    def test_condition_with_children_raises(self):
        with pytest.raises(ValueError, match="must not use children"):
            WorkflowNode(
                name="c",
                node_type=WorkflowNodeType.CONDITION,
                condition_cel="x",
                true_steps=[_step_node("t", "tool")],
                children=[_step_node("legacy", "tool-legacy")],
            )

    def test_condition_with_choices_raises(self):
        with pytest.raises(ValueError, match="must not define choices"):
            WorkflowNode(
                name="c",
                node_type=WorkflowNodeType.CONDITION,
                condition_cel="x",
                true_steps=[_step_node("t", "tool")],
                choices=[RouterChoice(name="x", steps=[_step_node("inner", "tool-inner")])],
            )

    def test_condition_false_steps_optional(self):
        node = WorkflowNode(
            name="c",
            node_type=WorkflowNodeType.CONDITION,
            condition_cel="x",
            true_steps=[_step_node("t", "tool")],
        )
        assert node.false_steps == []


@pytest.mark.unit
class TestRouterNodeValidation:
    def test_router_with_two_single_step_choices_is_valid(self):
        node = WorkflowNode(
            name="r",
            node_type=WorkflowNodeType.ROUTER,
            condition_cel="step_choices[0]",
            choices=[
                RouterChoice(name="a", steps=[_step_node("a", "tool-a")]),
                RouterChoice(name="b", steps=[_step_node("b", "tool-b")]),
            ],
        )
        assert len(node.choices) == 2

    def test_router_with_multi_step_choices_is_valid(self):
        node = WorkflowNode(
            name="r",
            node_type=WorkflowNodeType.ROUTER,
            condition_cel="step_choices[0]",
            choices=[
                RouterChoice(name="a", steps=[_step_node("a1", "tool-a1"), _step_node("a2", "tool-a2")]),
                RouterChoice(name="b", steps=[_step_node("b1", "tool-b1")]),
            ],
        )
        assert [c.name for c in node.choices] == ["a", "b"]
        assert [s.name for s in node.choices[0].steps] == ["a1", "a2"]

    def test_router_requires_at_least_two_choices(self):
        with pytest.raises(ValueError, match="at least 2 choices"):
            WorkflowNode(
                name="r",
                node_type=WorkflowNodeType.ROUTER,
                condition_cel="step_choices[0]",
                choices=[RouterChoice(name="a", steps=[_step_node("a", "tool-a")])],
            )

    def test_router_choices_must_have_unique_names(self):
        with pytest.raises(ValueError, match="router choices must have unique names"):
            WorkflowNode(
                name="r",
                node_type=WorkflowNodeType.ROUTER,
                condition_cel="step_choices[0]",
                choices=[
                    RouterChoice(name="dup", steps=[_step_node("a", "tool-a")]),
                    RouterChoice(name="dup", steps=[_step_node("b", "tool-b")]),
                ],
            )

    def test_router_with_children_raises(self):
        with pytest.raises(ValueError, match="must not use children"):
            WorkflowNode(
                name="r",
                node_type=WorkflowNodeType.ROUTER,
                condition_cel="step_choices[0]",
                choices=[
                    RouterChoice(name="a", steps=[_step_node("a", "tool-a")]),
                    RouterChoice(name="b", steps=[_step_node("b", "tool-b")]),
                ],
                children=[_step_node("legacy", "tool-legacy")],
            )

    def test_router_choice_requires_at_least_one_step(self):
        with pytest.raises(ValueError, match="router choice requires at least one step"):
            RouterChoice(name="empty", steps=[])


@pytest.mark.unit
class TestNestedBranches:
    """Multi-branch / deep-nesting scenarios — guards against regression in recursive _build."""

    def test_condition_nested_inside_condition_true_branch(self):
        """B[cond] true: C → B2[cond] (true: E→G | false: F)   false: D"""
        inner_cond = WorkflowNode(
            name="B2",
            node_type=WorkflowNodeType.CONDITION,
            condition_cel="inner_flag",
            true_steps=[_step_node("E", "tool-e"), _step_node("G", "tool-g")],
            false_steps=[_step_node("F", "tool-f")],
        )
        outer = WorkflowNode(
            name="B",
            node_type=WorkflowNodeType.CONDITION,
            condition_cel="outer_flag",
            true_steps=[_step_node("C", "tool-c"), inner_cond],
            false_steps=[_step_node("D", "tool-d")],
        )

        workflow = compiler.compile_workflow(
            _workflow_definition([outer]),
            _workflow_run(),
            executor_registry={
                "tool-c": _executor,
                "tool-e": _executor,
                "tool-g": _executor,
                "tool-f": _executor,
                "tool-d": _executor,
            },
        )

        outer_cond = workflow.steps[0]
        assert type(outer_cond).__name__ == "Condition"
        assert [type(s).__name__ for s in outer_cond.steps] == ["Step", "Condition"]
        inner = outer_cond.steps[1]
        assert inner.evaluator == "inner_flag"
        assert [s.name for s in inner.steps] == ["E", "G"]
        assert [s.name for s in inner.else_steps] == ["F"]
        assert [s.name for s in outer_cond.else_steps] == ["D"]

    def test_parallel_inside_condition_true_branch(self):
        """B[cond] true: [PARALLEL(P1, P2)] → followup   (else: skip)"""
        parallel = WorkflowNode(
            name="par",
            node_type=WorkflowNodeType.PARALLEL,
            children=[_step_node("P1", "tool-p1"), _step_node("P2", "tool-p2")],
        )
        cond = WorkflowNode(
            name="B",
            node_type=WorkflowNodeType.CONDITION,
            condition_cel="run_parallel",
            true_steps=[parallel, _step_node("followup", "tool-fu")],
        )

        workflow = compiler.compile_workflow(
            _workflow_definition([cond]),
            _workflow_run(),
            executor_registry={
                "tool-p1": _executor,
                "tool-p2": _executor,
                "tool-fu": _executor,
            },
        )
        condition_step = workflow.steps[0]
        assert [type(s).__name__ for s in condition_step.steps] == ["Parallel", "Step"]
        parallel_step = condition_step.steps[0]
        assert [s.name for s in parallel_step.steps] == ["P1", "P2"]
        assert condition_step.else_steps is None

    def test_loop_inside_condition_false_branch(self):
        """B[cond] true: skip   false: [LOOP(body)]"""
        loop = WorkflowNode(
            name="retry-loop",
            node_type=WorkflowNodeType.LOOP,
            loop_config=LoopConfig(max_iterations=3, end_condition_cel="done"),
            children=[_step_node("body", "tool-body")],
        )
        cond = WorkflowNode(
            name="B",
            node_type=WorkflowNodeType.CONDITION,
            condition_cel="should_retry",
            true_steps=[_step_node("skip-step", "tool-skip")],
            false_steps=[loop],
        )

        workflow = compiler.compile_workflow(
            _workflow_definition([cond]),
            _workflow_run(),
            executor_registry={"tool-skip": _executor, "tool-body": _executor},
        )
        condition_step = workflow.steps[0]
        assert [type(s).__name__ for s in condition_step.else_steps] == ["Loop"]
        loop_step = condition_step.else_steps[0]
        assert loop_step.max_iterations == 3
        assert [s.name for s in loop_step.steps] == ["body"]

    def test_router_with_three_choices_compiles(self):
        """ROUTER with 3 mixed-arity choices."""
        router = WorkflowNode(
            name="rt",
            node_type=WorkflowNodeType.ROUTER,
            condition_cel="step_choices[0]",
            choices=[
                RouterChoice(name="single", steps=[_step_node("s1", "tool-s1")]),
                RouterChoice(
                    name="pair",
                    steps=[_step_node("p1", "tool-p1"), _step_node("p2", "tool-p2")],
                ),
                RouterChoice(
                    name="triple",
                    steps=[
                        _step_node("t1", "tool-t1"),
                        _step_node("t2", "tool-t2"),
                        _step_node("t3", "tool-t3"),
                    ],
                ),
            ],
        )

        workflow = compiler.compile_workflow(
            _workflow_definition([router]),
            _workflow_run(),
            executor_registry={
                "tool-s1": _executor,
                "tool-p1": _executor,
                "tool-p2": _executor,
                "tool-t1": _executor,
                "tool-t2": _executor,
                "tool-t3": _executor,
            },
        )
        router_step = workflow.steps[0]
        # All choices wrapped in Steps; container names always match RouterChoice.name.
        assert [type(c).__name__ for c in router_step.choices] == ["Steps", "Steps", "Steps"]
        assert [c.name for c in router_step.choices] == ["single", "pair", "triple"]
        assert [s.name for s in router_step.choices[0].steps] == ["s1"]
        assert [s.name for s in router_step.choices[1].steps] == ["p1", "p2"]
        assert [s.name for s in router_step.choices[2].steps] == ["t1", "t2", "t3"]

    def test_router_choice_containing_nested_condition(self):
        """ROUTER → choice 'tech' → CONDITION inside (true: E→G | false: F)"""
        inner_cond = WorkflowNode(
            name="inner-cond",
            node_type=WorkflowNodeType.CONDITION,
            condition_cel="severity > 5",
            true_steps=[_step_node("E", "tool-e"), _step_node("G", "tool-g")],
            false_steps=[_step_node("F", "tool-f")],
        )
        router = WorkflowNode(
            name="rt",
            node_type=WorkflowNodeType.ROUTER,
            condition_cel="step_choices[0]",
            choices=[
                RouterChoice(name="tech", steps=[_step_node("triage", "tool-triage"), inner_cond]),
                RouterChoice(name="general", steps=[_step_node("ack", "tool-ack")]),
            ],
        )

        workflow = compiler.compile_workflow(
            _workflow_definition([router]),
            _workflow_run(),
            executor_registry={
                "tool-triage": _executor,
                "tool-e": _executor,
                "tool-g": _executor,
                "tool-f": _executor,
                "tool-ack": _executor,
            },
        )
        router_step = workflow.steps[0]
        tech_choice = router_step.choices[0]
        # Multi-step choice wrapped in Steps; inner contents are Step, Condition
        assert type(tech_choice).__name__ == "Steps"
        assert tech_choice.name == "tech"
        assert [type(s).__name__ for s in tech_choice.steps] == ["Step", "Condition"]
        nested_cond = tech_choice.steps[1]
        assert [s.name for s in nested_cond.steps] == ["E", "G"]
        assert [s.name for s in nested_cond.else_steps] == ["F"]

    def test_flatten_walks_deeply_nested_tree(self):
        """flatten covers children + true_steps + false_steps + choices.steps at any depth."""
        # ROOT (parallel)
        # ├── P1 (step)
        # └── COND (condition cel="x")
        #     ├── true_steps: [T1, ROUTER]
        #     │                 ROUTER ├── choice "a": [A1, A2]
        #     │                        └── choice "b": [B1]
        #     └── false_steps: [LOOP(body)]
        deep_router = WorkflowNode(
            name="ROUTER",
            node_type=WorkflowNodeType.ROUTER,
            condition_cel="step_choices[0]",
            choices=[
                RouterChoice(name="a", steps=[_step_node("A1", "tool-a1"), _step_node("A2", "tool-a2")]),
                RouterChoice(name="b", steps=[_step_node("B1", "tool-b1")]),
            ],
        )
        loop = WorkflowNode(
            name="LOOP",
            node_type=WorkflowNodeType.LOOP,
            loop_config=LoopConfig(max_iterations=2),
            children=[_step_node("body", "tool-body")],
        )
        cond = WorkflowNode(
            name="COND",
            node_type=WorkflowNodeType.CONDITION,
            condition_cel="x",
            true_steps=[_step_node("T1", "tool-t1"), deep_router],
            false_steps=[loop],
        )
        root = WorkflowNode(
            name="ROOT",
            node_type=WorkflowNodeType.PARALLEL,
            children=[_step_node("P1", "tool-p1"), cond],
        )

        flat = [n.name for n in compiler.flatten_workflow_nodes([root])]
        # Expected order matches the recursion order in flatten_workflow_nodes:
        # node → children → true_steps → false_steps → choices.steps
        assert flat == [
            "ROOT",
            "P1",
            "COND",
            "T1",
            "ROUTER",
            "A1",
            "A2",
            "B1",
            "LOOP",
            "body",
        ]


@pytest.mark.unit
class TestEnumDriftAgainstAgno:
    """Anti-corruption layer: surface agno enum drift the moment it happens.

    Our OnRejectPolicy / OnTimeoutPolicy are the stable contract (API + DB).
    The compiler translates them to agno's string values via _ON_REJECT_TO_AGNO
    and _ON_TIMEOUT_TO_AGNO.  These tests fail loudly if either side changes
    members so we never silently pass an invalid value into agno.
    """

    def test_on_reject_keys_cover_our_enum(self):
        from registry_pkgs.models.enums import OnRejectPolicy
        from registry_pkgs.workflows.compiler import _ON_REJECT_TO_AGNO

        assert set(_ON_REJECT_TO_AGNO.keys()) == set(OnRejectPolicy)

    def test_on_reject_values_are_accepted_by_agno(self):
        from agno.workflow.types import OnReject

        from registry_pkgs.workflows.compiler import _ON_REJECT_TO_AGNO

        agno_values = {member.value for member in OnReject}
        for ours, agno_str in _ON_REJECT_TO_AGNO.items():
            assert agno_str in agno_values, f"OnRejectPolicy.{ours.name} → {agno_str!r} not in agno OnReject"

    def test_on_timeout_keys_cover_our_enum(self):
        from registry_pkgs.models.enums import OnTimeoutPolicy
        from registry_pkgs.workflows.compiler import _ON_TIMEOUT_TO_AGNO

        assert set(_ON_TIMEOUT_TO_AGNO.keys()) == set(OnTimeoutPolicy)

    def test_on_timeout_values_are_accepted_by_agno(self):
        from agno.workflow.types import OnTimeout

        from registry_pkgs.workflows.compiler import _ON_TIMEOUT_TO_AGNO

        agno_values = {member.value for member in OnTimeout}
        for ours, agno_str in _ON_TIMEOUT_TO_AGNO.items():
            assert agno_str in agno_values, f"OnTimeoutPolicy.{ours.name} → {agno_str!r} not in agno OnTimeout"
