"""Exercise continuation through the production compiler and real agno dispatch."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from agno.db.in_memory import InMemoryDb
from agno.workflow import Router, Step, StepInput, StepOutput, Workflow
from beanie import PydanticObjectId

from registry_pkgs.models.enums import WorkflowRunStatus
from registry_pkgs.models.workflow import (
    HumanReviewSpec,
    RouterChoice,
    StepConfig,
    WorkflowDefinition,
    WorkflowNode,
    WorkflowRun,
)
from registry_pkgs.workflows.agno_compat import RegistryWorkflow
from registry_pkgs.workflows.compiler import compile_workflow, flatten_workflow_nodes
from registry_pkgs.workflows.control import DirectiveQueue
from registry_pkgs.workflows.control import wrapper as wrapper_module
from registry_pkgs.workflows.hitl import hydrate_requirement, serialize_requirement
from registry_pkgs.workflows.persistence import WorkflowRunSyncer, _flatten_step_results
from registry_pkgs.workflows.prompt import ADDITIONAL_DATA_STEP_OBJECTIVE


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["raise", "return_failure", "retry", "skip", "success"])
@pytest.mark.parametrize("output_review", [False, True])
async def test_manual_router_continuation_honors_stop(
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
    output_review: bool,
) -> None:
    executed: list[str] = []

    async def selected_executor(step_input: StepInput, session_state: dict | None = None) -> StepOutput:
        executed.append("selected")
        if outcome == "success":
            return StepOutput(content="selected output")
        if outcome == "return_failure":
            return StepOutput(success=False, error="original auth error")
        raise RuntimeError("original auth error")

    async def record_executor(step_input: StepInput, session_state: dict | None = None) -> StepOutput:
        name = step_input.additional_data[ADDITIONAL_DATA_STEP_OBJECTIVE]
        executed.append(name)
        return StepOutput(content=name)

    def node(name: str) -> WorkflowNode:
        return WorkflowNode(name=name, executor_key="fixture", step_objective=name)

    selected = node("selected")
    selected.step_config = StepConfig(
        on_error=outcome if outcome in {"retry", "skip"} else "fail",
        max_retries=2 if outcome == "retry" else 0,
        backoff_base_seconds=0.001,
    )
    route = WorkflowNode(
        name="router",
        node_type="router",
        condition_cel='"other"',  # The user's selection must override the default route.
        human_review=HumanReviewSpec(requires_user_input=True, requires_output_review=output_review),
        choices=[
            RouterChoice(name="chosen", steps=[selected, node("inner-after")]),
            RouterChoice(name="other", steps=[node("unselected")]),
        ],
    )
    definition = WorkflowDefinition.model_construct(
        id=PydanticObjectId(), name="manual-route", nodes=[node("before"), route, node("downstream")]
    )
    run = WorkflowRun.model_construct(id=PydanticObjectId(), workflow_definition_id=definition.id)
    nodes = flatten_workflow_nodes(definition.nodes)
    executors = {item.id: record_executor for item in nodes if item.executor_key}
    executors[selected.id] = selected_executor
    queue = DirectiveQueue()
    db = InMemoryDb()
    monkeypatch.setattr(wrapper_module, "_read_mongodb_directive", AsyncMock(return_value=None))
    monkeypatch.setattr(wrapper_module, "_record_attempt_start", AsyncMock())
    monkeypatch.setattr(wrapper_module, "_record_attempt_result", AsyncMock())

    def compile_run() -> Workflow:
        workflow = compile_workflow(definition, run, executor_registry=executors, directive_queue=queue)
        workflow.db = db
        workflow.telemetry = False
        return workflow

    paused = await compile_run().arun(input="hello", session_id=str(run.id))
    assert paused.is_paused
    assert executed == ["before"]
    requirement = paused.step_requirements[-1]
    assert requirement.requires_route_selection
    requirement.select("chosen")

    # Production rebuilds the workflow and hydrates the decision on another request/worker.
    workflow = compile_run()
    result = await workflow.acontinue_run(
        run_id=paused.run_id,
        session_id=str(run.id),
        step_requirements=[hydrate_requirement(serialize_requirement(requirement))],
    )
    assert workflow.steps[1].selector == '"other"'
    assert executed.count("before") == 1
    assert "unselected" not in executed
    assert executed.count("selected") == (3 if outcome == "retry" else 1)

    run_doc = SimpleNamespace(
        id=run.id,
        status=WorkflowRunStatus.RUNNING,
        error_summary=None,
        pending_requirements=[serialize_requirement(requirement)],
        finished_at=None,
        final_output=None,
        save=AsyncMock(),
    )
    syncer = object.__new__(WorkflowRunSyncer)
    syncer._workflow_run = run_doc
    syncer._node_by_name = {item.name: item for item in nodes}
    await syncer._update_workflow_run(result, _flatten_step_results(result.step_results))

    if outcome not in {"skip", "success"}:
        assert "inner-after" not in executed
        assert "downstream" not in executed
        assert result.step_results[-1].stop is True
        assert run_doc.status == WorkflowRunStatus.FAILED
        assert run_doc.error_summary == "original auth error"
        assert run_doc.pending_requirements == []
    else:
        assert executed.count("inner-after") == 1
        if output_review:
            assert result.is_paused
            assert "downstream" not in executed
            assert run_doc.status == WorkflowRunStatus.PAUSED
            result.step_requirements[-1].confirm()
            result = await workflow.acontinue_run(result)
            await syncer._update_workflow_run(result, _flatten_step_results(result.step_results))
        assert executed.count("selected") == 1
        assert executed.count("downstream") == 1
        assert run_doc.status == WorkflowRunStatus.COMPLETED
        assert run_doc.error_summary is None
        assert all(output.stop is False for output in _flatten_step_results(result.step_results))


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [RuntimeError, asyncio.CancelledError])
async def test_manual_router_restores_selector_on_exception(
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[BaseException],
) -> None:
    chosen = Step(name="chosen", executor=AsyncMock())
    router = Router(name="router", selector='"other"', choices=[chosen])
    workflow = RegistryWorkflow(steps=[router], telemetry=False)

    async def fail_continuation(self: Workflow, **kwargs: object) -> None:
        assert "router_selection" not in kwargs
        assert router.selector(None) == [chosen]
        raise error_type("interrupted")

    monkeypatch.setattr(Workflow, "_acontinue_execute", fail_continuation)
    with pytest.raises(error_type, match="interrupted"):
        await workflow._acontinue_execute(
            session=None,
            execution_input=None,
            workflow_run_response=None,
            run_context=None,
            start_step_index=0,
            router_selection=["chosen"],
        )
    assert router.selector == '"other"'
