"""Real-Mongo end-to-end persistence test for the agno 3 upgrade (AS-1996).

Gated on ``MONGO_INTEGRATION_URI`` like ``test_leased_job.py``. agno 3 stores
each run as its own document in ``<session>_runs`` and the terminal sync path
opens its own transaction, so this must run against a replica-set Mongo (the
repo's ``docker compose`` Mongo). It drives ``compile_workflow`` + ``arun`` with
stub executors so the real ``WorkflowRunSyncer`` writes ``WorkflowRun`` /
``NodeRun`` state, covering the regressions agno internals mock away in the
unit suite.
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
import pytest_asyncio
from agno.workflow import StepInput, StepOutput
from beanie import PydanticObjectId, init_beanie
from pymongo import AsyncMongoClient

from registry_pkgs.models.enums import NodeRunStatus, WorkflowRunStatus
from registry_pkgs.models.workflow import (
    HumanReviewSpec,
    NodeRun,
    StepConfig,
    WorkflowDefinition,
    WorkflowNode,
    WorkflowRun,
)
from registry_pkgs.workflows.compiler import compile_workflow, flatten_workflow_nodes
from registry_pkgs.workflows.hitl import hydrate_requirement, serialize_requirement
from registry_pkgs.workflows.persistence import AGNO_RUNS_COLLECTION

pytestmark = pytest.mark.asyncio


async def _ok(step_input: StepInput, session_state: dict | None = None) -> StepOutput:
    return StepOutput(content="ok")


async def _fail(step_input: StepInput, session_state: dict | None = None) -> StepOutput:
    # A failed StepOutput is what agno's on_error policy (now on HumanReview) acts on.
    # A raised exception is a different path caught by the runner / control wrapper.
    return StepOutput(success=False, error="boom")


@pytest_asyncio.fixture
async def mongo():
    uri = os.environ.get("MONGO_INTEGRATION_URI")
    if not uri:
        pytest.skip("Set MONGO_INTEGRATION_URI (replica-set Mongo) to run agno3 persistence tests")
    db_name = f"agno3_persist_test_{uuid4().hex}"
    client = AsyncMongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=3000)
    # init_beanie must bind to the SAME client the syncer uses, so the terminal
    # transaction's session and the NodeRun/WorkflowRun collections share a client.
    await init_beanie(database=client[db_name], document_models=[WorkflowRun, NodeRun])
    try:
        yield client, db_name
    finally:
        await client.drop_database(db_name)
        await client.close()


def _definition(middle: WorkflowNode) -> WorkflowDefinition:
    return WorkflowDefinition.model_construct(
        id=PydanticObjectId(),
        name="agno3-persist",
        nodes=[
            WorkflowNode(name="first", executor_key="k1", step_objective="first"),
            middle,
            WorkflowNode(name="third", executor_key="k3", step_objective="third"),
        ],
    )


def _registry(definition: WorkflowDefinition, middle_executor) -> dict[str, object]:
    executors_by_key = {"k1": _ok, "k2": middle_executor, "k3": _ok}
    registry: dict[str, object] = {}
    for node in flatten_workflow_nodes(definition.nodes):
        if node.executor_key in executors_by_key:
            registry[node.id] = executors_by_key[node.executor_key]
    return registry


async def _insert_running_run(definition: WorkflowDefinition) -> WorkflowRun:
    run = WorkflowRun(
        workflow_definition_id=definition.id,
        workflow_version=1,
        status=WorkflowRunStatus.RUNNING,
    )
    await run.insert()
    return run


def _compile(definition, run, client, db_name, middle_executor):
    workflow = compile_workflow(
        definition,
        run,
        executor_registry=_registry(definition, middle_executor),
        db_client=client,
        db_name=db_name,
    )
    workflow.telemetry = False
    return workflow


async def test_all_steps_succeed_completes_with_node_runs_and_run_doc(mongo):
    client, db_name = mongo
    definition = _definition(WorkflowNode(name="second", executor_key="k2", step_objective="second"))
    run = await _insert_running_run(definition)

    await _compile(definition, run, client, db_name, _ok).arun(input="go", session_id=str(run.id))

    fresh = await WorkflowRun.get(run.id)
    assert fresh is not None
    assert fresh.status == WorkflowRunStatus.COMPLETED

    node_runs = await NodeRun.find(NodeRun.workflow_run_id == run.id).to_list()
    assert len(node_runs) == 3
    assert {nr.status for nr in node_runs} == {NodeRunStatus.COMPLETED}

    # (d) agno 3 wrote the run as its own document in the runs collection.
    run_docs = await client[db_name][AGNO_RUNS_COLLECTION].count_documents({"session_id": str(run.id)})
    assert run_docs >= 1


async def test_failing_step_on_error_fail_fails_run(mongo):
    client, db_name = mongo
    middle = WorkflowNode(name="second", executor_key="k2", step_objective="second")
    middle.step_config = StepConfig(on_error="fail")
    definition = _definition(middle)
    run = await _insert_running_run(definition)

    await _compile(definition, run, client, db_name, _fail).arun(input="go", session_id=str(run.id))

    fresh = await WorkflowRun.get(run.id)
    assert fresh is not None
    assert fresh.status == WorkflowRunStatus.FAILED


async def test_failing_step_on_error_skip_continues_run(mongo):
    # The counterpart to on_error="fail": a skip-tolerated failure must not fail the
    # run. This is the exact behaviour the agno 3 error-policy move must preserve.
    client, db_name = mongo
    middle = WorkflowNode(name="second", executor_key="k2", step_objective="second")
    middle.step_config = StepConfig(on_error="skip")
    definition = _definition(middle)
    run = await _insert_running_run(definition)

    await _compile(definition, run, client, db_name, _fail).arun(input="go", session_id=str(run.id))

    fresh = await WorkflowRun.get(run.id)
    assert fresh is not None
    assert fresh.status == WorkflowRunStatus.COMPLETED

    node_runs = await NodeRun.find(NodeRun.workflow_run_id == run.id).to_list()
    skipped = {nr.node_name: nr.status for nr in node_runs}
    assert skipped["second"] == NodeRunStatus.SKIPPED


async def test_hitl_confirmation_pauses_then_resumes_to_completed(mongo):
    client, db_name = mongo
    middle = WorkflowNode(name="second", executor_key="k2", step_objective="second")
    middle.human_review = HumanReviewSpec(requires_confirmation=True)
    definition = _definition(middle)
    run = await _insert_running_run(definition)

    paused = await _compile(definition, run, client, db_name, _ok).arun(input="go", session_id=str(run.id))
    assert paused.is_paused

    after_pause = await WorkflowRun.get(run.id)
    assert after_pause is not None
    assert after_pause.status == WorkflowRunStatus.PAUSED

    requirement = paused.step_requirements[-1]
    requirement.confirm()

    workflow = _compile(definition, run, client, db_name, _ok)
    await workflow.acontinue_run(
        run_id=paused.run_id,
        session_id=str(run.id),
        step_requirements=[hydrate_requirement(serialize_requirement(requirement))],
    )

    resumed = await WorkflowRun.get(run.id)
    assert resumed is not None
    assert resumed.status == WorkflowRunStatus.COMPLETED
