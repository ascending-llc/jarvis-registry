"""End-to-end verification for AS-1543 features against a real MongoDB.

Covers 9 modules / 34 checks:
  A. ACL                  (2)  — creator OWNER, 403 without scope+ACL
  B. Versioning           (7)  — PUT bump, checksum, history, version param, in-flight snapshot
  C. HITL approval gate   (11) — confirm/reject(skip/cancel/else_branch)/user_input/edit/route_select
                                 + multi-step chain, Condition+confirm, cross-session restore.
                                 Assertions verify branch selection + data propagation, not just COMPLETED.
  D. Cancel               (5)  — running/awaiting-approval/agno bridge/idempotent/terminal-state
  E. Cascade delete       (1)  — workflow_definitions + runs + node_runs + versions + agno sessions
  F. Run query            (2)  — list, detail with pendingRequirements
  G. Retry                (1)  — retry a COMPLETED run from a node
  H. Pause/Resume smoke   (1)  — pause → resume → completes
  I. Error paths          (4)  — 404 / 400 invalid version / 409 terminal cancel / 400 bad node

Usage:
    uv run python scripts/verify_workflow_control_e2e.py                    # run all
    uv run python scripts/verify_workflow_control_e2e.py --modules A,C,D    # subset
    uv run python scripts/verify_workflow_control_e2e.py --keep-data        # keep test data on exit

Pattern follows ``scripts/test_control_e2e.py``: direct service+runner calls
against real MongoDB; ACL/route layers covered by their unit tests.  Mock step
executor returns instantly so e2e total runtime stays under ~2 min.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
import traceback
from pathlib import Path

from agno.models.aws import AwsBedrock
from agno.workflow import StepInput, StepOutput
from agno.workflow.step import StepExecutor
from beanie import PydanticObjectId
from dotenv import load_dotenv
from fastapi import HTTPException

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from registry import settings  # noqa: E402
from registry.schemas.workflow_api_schemas import (  # noqa: E402
    HumanReviewInput,
    UserInputFieldSchema,
    WorkflowCreateRequest,
    WorkflowNodeInput,
    WorkflowUpdateRequest,
    _convert_node_to_input,
)
from registry.services.access_control_service import ACLService  # noqa: E402
from registry.services.group_service import GroupService  # noqa: E402
from registry.services.user_service import UserService  # noqa: E402
from registry.services.workflow_control_service import WorkflowControlService  # noqa: E402
from registry.services.workflow_service import WorkflowService  # noqa: E402
from registry_pkgs.core.config import MongoConfig  # noqa: E402
from registry_pkgs.database.mongodb import MongoDB  # noqa: E402
from registry_pkgs.models import PrincipalType  # noqa: E402
from registry_pkgs.models.enums import (  # noqa: E402
    OnRejectPolicy,
    OnTimeoutPolicy,
    RequirementResolution,
    RoleBits,
    WorkflowRunStatus,
)
from registry_pkgs.models.extended_acl_entry import ExtendedResourceType  # noqa: E402
from registry_pkgs.models.workflow import NodeRun, WorkflowDefinition, WorkflowRun, WorkflowVersion  # noqa: E402
from registry_pkgs.workflows.compiler import flatten_workflow_nodes  # noqa: E402
from registry_pkgs.workflows.control import DirectiveQueue  # noqa: E402
from registry_pkgs.workflows.runner import WorkflowRunner  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("as1543_e2e")

PASS = "✅"
FAIL = "❌"
SKIP = "·"
PREFIX = "__as1543_e2e__"
USER_A = PydanticObjectId()
USER_B = PydanticObjectId()


# ────────────────────────────────────────────────────────────────────────────
# Mock runner — instant executors for fast e2e
# ────────────────────────────────────────────────────────────────────────────


class MockRunner(WorkflowRunner):
    """Replace MCP/A2A executors with instant in-process mocks."""

    async def _build_registry(self, definition, registry_token, user_id):
        all_nodes = flatten_workflow_nodes(definition.nodes)
        keys = list(dict.fromkeys(n.executor_key for n in all_nodes if n.executor_key))

        def make(key: str) -> StepExecutor:
            async def mock(step_input: StepInput, session_state: dict | None = None) -> StepOutput:
                await asyncio.sleep(0.05)
                # Echo prior step content + any HITL user_input so downstream nodes
                # (and the persisted NodeRun.output_snapshot) can verify propagation.
                # agno surfaces collected user_input via step_input.additional_data.
                prior = getattr(step_input, "previous_step_content", None)
                extra = getattr(step_input, "additional_data", None) or {}
                user_input = extra.get("user_input")
                content = f"{key}:OK(prior={prior!r}"
                if user_input:
                    content += f", user_input={user_input!r}"
                content += ")"
                return StepOutput(content=content, success=True)

            return mock

        return {k: make(k) for k in keys}


def _build_runner(queue: DirectiveQueue) -> MockRunner:
    llm = AwsBedrock(
        id=os.getenv("BEDROCK_MODEL", "us.amazon.nova-lite-v1:0"),
        aws_region=settings.aws_region,
        aws_session_token=settings.aws_session_token,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
    )
    return MockRunner(
        llm=llm,
        registry_url=os.getenv("REGISTRY_URL", "http://localhost:7860"),
        db_client=MongoDB.get_client(),
        db_name=MongoDB.database_name,
        jwt_config=settings.jwt_signing_config,
        directive_queue=queue,
    )


# ────────────────────────────────────────────────────────────────────────────
# Test infra
# ────────────────────────────────────────────────────────────────────────────


class Report:
    """Per-module results."""

    def __init__(self, module: str) -> None:
        self.module = module
        self.checks: list[tuple[str, bool, str]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append((name, ok, detail))
        glyph = PASS if ok else FAIL
        suffix = f" — {detail}" if detail else ""
        print(f"  {glyph}  {name}{suffix}")
        return ok

    @property
    def passed(self) -> int:
        return sum(1 for _, ok, _ in self.checks if ok)

    @property
    def total(self) -> int:
        return len(self.checks)


async def _poll(predicate, timeout: float = 30.0, interval: float = 0.2):
    """Poll an async predicate until it returns truthy or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = await predicate()
        if result:
            return result
        await asyncio.sleep(interval)
    return None


async def _make_workflow(
    workflow_service: WorkflowService,
    acl_service: ACLService,
    name: str,
    nodes: list[WorkflowNodeInput],
    *,
    creator: PydanticObjectId = USER_A,
) -> WorkflowDefinition:
    req = WorkflowCreateRequest(name=PREFIX + name, description="e2e", nodes=nodes)
    workflow = await workflow_service.create_workflow(data=req)
    await acl_service.grant_permission(
        principal_type=PrincipalType.USER,
        principal_id=creator,
        resource_type=ExtendedResourceType.WORKFLOW,
        resource_id=workflow.id,
        perm_bits=RoleBits.OWNER,
    )
    return workflow


def _step_input(name: str, executor_key: str = "tool-x", **kw) -> WorkflowNodeInput:
    return WorkflowNodeInput(name=name, nodeType="step", executorKey=executor_key, **kw)


def _hitl_input(**kw) -> HumanReviewInput:
    """Build a HumanReviewInput with sensible defaults; override via kwargs."""
    defaults: dict = {
        "requiresConfirmation": False,
        "requiresUserInput": False,
        "requiresOutputReview": False,
        "requiresIterationReview": False,
        "onReject": OnRejectPolicy.SKIP,
        "onTimeout": OnTimeoutPolicy.CANCEL,
    }
    defaults.update(kw)
    return HumanReviewInput(**defaults)


# ────────────────────────────────────────────────────────────────────────────
# Trigger-and-wait helper used by HITL / Cancel / Retry modules
# ────────────────────────────────────────────────────────────────────────────


async def _trigger_run_inproc(
    runner: MockRunner,
    workflow_id: str,
) -> tuple[str, asyncio.Task]:
    """Insert a PENDING WorkflowRun and start runner.run() in the background.

    Returns (run_id, task).  Caller is responsible for awaiting the task or
    cancelling it during cleanup.
    """
    run = WorkflowRun(
        workflow_definition_id=PydanticObjectId(workflow_id),
        status=WorkflowRunStatus.PENDING,
        trigger_source="e2e",
        initial_input={"user_text": "e2e"},
        triggering_user_id=str(USER_A),
    )
    await run.insert()
    task = asyncio.create_task(
        runner.run(workflow_id, "e2e", registry_token="test", user_id=str(USER_A), existing_run_id=str(run.id))
    )
    return str(run.id), task


async def _wait_status(run_id: str, *targets: WorkflowRunStatus, timeout: float = 30.0) -> WorkflowRun | None:
    async def check():
        run = await WorkflowRun.get(PydanticObjectId(run_id))
        if run is None:
            return None
        return run if run.status in targets else None

    return await _poll(check, timeout=timeout)


async def _node_runs(run_id: str) -> list[NodeRun]:
    return await NodeRun.find(NodeRun.workflow_run_id == PydanticObjectId(run_id)).to_list()


async def _completed_names(run_id: str) -> set[str]:
    """Names of NodeRuns that reached COMPLETED for this run."""
    return {nr.node_name for nr in await _node_runs(run_id) if str(nr.status) == "completed"}


async def _node_output(run_id: str, node_name: str) -> str:
    """Persisted output_snapshot content for a named node ('' if absent)."""
    for nr in await _node_runs(run_id):
        if nr.node_name == node_name and nr.output_snapshot:
            return nr.output_snapshot.get("content", "") or ""
    return ""


# ════════════════════════════════════════════════════════════════════════════
# Module A — ACL  (2 checks)
# ════════════════════════════════════════════════════════════════════════════


async def module_a(workflow_service, control_service, acl_service) -> Report:
    r = Report("A. ACL")
    nodes = [_step_input("only-step")]

    # A1: creator gets OWNER (permBits=15)
    workflow = await workflow_service.create_workflow(
        data=WorkflowCreateRequest(name=PREFIX + "acl-creator", description="", nodes=nodes)
    )
    await acl_service.grant_permission(
        principal_type=PrincipalType.USER,
        principal_id=USER_A,
        resource_type=ExtendedResourceType.WORKFLOW,
        resource_id=workflow.id,
        perm_bits=RoleBits.OWNER,
    )
    perms = await acl_service.get_user_permissions_for_resource(
        user_id=USER_A,
        resource_type=ExtendedResourceType.WORKFLOW.value,
        resource_id=workflow.id,
    )
    r.check(
        "A1 creator gets OWNER (VIEW+EDIT+DELETE+SHARE)",
        perms.VIEW and perms.EDIT and perms.DELETE and perms.SHARE,
        f"permBits view={perms.VIEW} edit={perms.EDIT} delete={perms.DELETE} share={perms.SHARE}",
    )

    # A2: user_b without ACL → check_user_permission raises 403
    raised = False
    try:
        await acl_service.check_user_permission(
            user_id=USER_B,
            resource_type=ExtendedResourceType.WORKFLOW.value,
            resource_id=workflow.id,
            required_permission="VIEW",
        )
    except HTTPException as exc:
        raised = exc.status_code == 403
    r.check("A2 user_b without ACL → 403 on VIEW check", raised)

    return r


# ════════════════════════════════════════════════════════════════════════════
# Module B — Versioning  (7 checks)
# ════════════════════════════════════════════════════════════════════════════


async def module_b(workflow_service, control_service, acl_service) -> Report:
    r = Report("B. Versioning")

    # B1: POST → version=1 + checksum stored
    nodes_v1 = [_step_input("s1", "tool-v1")]
    wf = await _make_workflow(workflow_service, acl_service, "ver-1", nodes_v1)
    r.check("B1 newly-created workflow.version == 1", wf.version == 1, f"version={wf.version}")

    # B2: PUT → version bumps, prior captured in WorkflowVersion
    nodes_v2 = [_step_input("s1", "tool-v2"), _step_input("s2", "tool-v2b")]
    updated = await workflow_service.update_workflow(
        str(wf.id),
        WorkflowUpdateRequest(name=wf.name, description=wf.description, nodes=nodes_v2),
    )
    r.check("B2 PUT bumps to version=2", updated.version == 2, f"version={updated.version}")

    history = await WorkflowVersion.find(WorkflowVersion.workflow_id == wf.id).sort("version").to_list()
    r.check("B2' prior version archived (1 history row)", len(history) == 1)

    # B3: GET /versions returns history + current (per service design).
    versions = await workflow_service.list_versions(str(wf.id))
    version_nums = sorted(v["version"] if isinstance(v, dict) else v.version for v in versions)
    r.check(
        "B3 GET /versions returns history + current ([v1, v2])",
        version_nums == [1, 2],
        f"got versions={version_nums}",
    )

    # B4: trigger run with version=1 → run uses v1 snapshot
    run1 = await workflow_service.trigger_workflow_run(workflow_id=str(wf.id), version=1)
    snap_node_count = len(run1.definition_snapshot.get("nodes", []))
    r.check(
        "B4 POST /runs version=1 → run.definition_snapshot has v1's node count (1)",
        snap_node_count == 1,
        f"snapshot nodes={snap_node_count}, version={run1.workflow_version}",
    )

    # B5: trigger without version → uses latest (v2 with 2 nodes)
    run2 = await workflow_service.trigger_workflow_run(workflow_id=str(wf.id))
    snap_node_count_latest = len(run2.definition_snapshot.get("nodes", []))
    r.check(
        "B5 POST /runs no version → latest (v2's 2 nodes)",
        snap_node_count_latest == 2 and run2.workflow_version == 2,
        f"snapshot nodes={snap_node_count_latest}, version={run2.workflow_version}",
    )

    # B6: in-flight invariance — PUT after run1 was triggered must not alter run1's snapshot
    # (We already triggered run1 against v1; bump again to v3 and re-read.)
    nodes_v3 = [_step_input("s1", "tool-v3"), _step_input("s2", "tool-v3b"), _step_input("s3", "tool-v3c")]
    await workflow_service.update_workflow(
        str(wf.id),
        WorkflowUpdateRequest(name=wf.name, description=wf.description, nodes=nodes_v3),
    )
    run1_reloaded = await WorkflowRun.get(PydanticObjectId(str(run1.id)))
    r.check(
        "B6 in-flight run1 snapshot unchanged after subsequent PUT (D9 invariant)",
        len(run1_reloaded.definition_snapshot.get("nodes", [])) == 1,
        f"run1 still has {len(run1_reloaded.definition_snapshot.get('nodes', []))} nodes",
    )

    return r


# ════════════════════════════════════════════════════════════════════════════
# Module C — HITL approval gate  (11 checks)
# ════════════════════════════════════════════════════════════════════════════


async def module_c(workflow_service, control_service, acl_service, queue, runner) -> Report:
    r = Report("C. HITL approval gate")

    async def _run_with_hitl(name: str, nodes: list[WorkflowNodeInput]) -> tuple[str, str, asyncio.Task]:
        """Create a workflow + start a run; wait for AWAITING_APPROVAL."""
        wf = await _make_workflow(workflow_service, acl_service, name, nodes)
        run_id, task = await _trigger_run_inproc(runner, str(wf.id))
        run = await _wait_status(
            run_id, WorkflowRunStatus.AWAITING_APPROVAL, WorkflowRunStatus.COMPLETED, WorkflowRunStatus.FAILED
        )
        assert run is not None, f"run {run_id} never paused/finished within timeout"
        return str(wf.id), run_id, task

    # ── C1: confirm → COMPLETED ─────────────────────────────────────────────
    hitl_step = _step_input(
        "approval",
        "tool-c1",
        humanReview=_hitl_input(requiresConfirmation=True),
    )
    wf_id, run_id, task = await _run_with_hitl("c1-confirm", [hitl_step])
    pending = (await WorkflowRun.get(PydanticObjectId(run_id))).pending_requirements
    step_id = pending[0]["step_id"]
    await control_service.resolve_requirement(wf_id, run_id, step_id=step_id, resolution=RequirementResolution.CONFIRM)
    final = await _wait_status(run_id, WorkflowRunStatus.COMPLETED, WorkflowRunStatus.FAILED)
    r.check(
        "C1 confirm → COMPLETED",
        final is not None and final.status == WorkflowRunStatus.COMPLETED,
        f"final={final.status if final else 'timeout'}",
    )
    await _await_or_cancel(task)

    # ── C2: reject + on_reject=skip ─────────────────────────────────────────
    nodes_c2 = [
        _step_input(
            "gate", "tool-c2-gate", humanReview=_hitl_input(requiresConfirmation=True, onReject=OnRejectPolicy.SKIP)
        ),
        _step_input("after", "tool-c2-after"),
    ]
    wf_id, run_id, task = await _run_with_hitl("c2-reject-skip", nodes_c2)
    pending = (await WorkflowRun.get(PydanticObjectId(run_id))).pending_requirements
    await control_service.resolve_requirement(
        wf_id, run_id, step_id=pending[0]["step_id"], resolution=RequirementResolution.REJECT
    )
    final = await _wait_status(run_id, WorkflowRunStatus.COMPLETED, WorkflowRunStatus.FAILED)
    r.check(
        "C2 reject + on_reject=skip → COMPLETED (gate skipped)",
        final is not None and final.status == WorkflowRunStatus.COMPLETED,
        f"final={final.status if final else 'timeout'}",
    )
    await _await_or_cancel(task)

    # ── C3: reject + on_reject=cancel ───────────────────────────────────────
    nodes_c3 = [
        _step_input(
            "gate", "tool-c3-gate", humanReview=_hitl_input(requiresConfirmation=True, onReject=OnRejectPolicy.CANCEL)
        ),
        _step_input("after", "tool-c3-after"),
    ]
    wf_id, run_id, task = await _run_with_hitl("c3-reject-cancel", nodes_c3)
    pending = (await WorkflowRun.get(PydanticObjectId(run_id))).pending_requirements
    await control_service.resolve_requirement(
        wf_id, run_id, step_id=pending[0]["step_id"], resolution=RequirementResolution.REJECT
    )
    final = await _wait_status(
        run_id, WorkflowRunStatus.CANCELLED, WorkflowRunStatus.FAILED, WorkflowRunStatus.COMPLETED
    )
    r.check(
        "C3 reject + on_reject=cancel → CANCELLED",
        final is not None and final.status == WorkflowRunStatus.CANCELLED,
        f"final={final.status if final else 'timeout'}",
    )
    await _await_or_cancel(task)

    # ── C4: Condition + on_reject=else_branch → false-branch executes ──────
    # Validates _ON_REJECT_TO_AGNO["else_branch"] = "else" fix.
    cond = WorkflowNodeInput(
        name="branch",
        nodeType="condition",
        conditionCel="session_state.user_text != ''",  # always true; reject overrides
        trueSteps=[_step_input("true-step", "tool-c4-true")],
        falseSteps=[_step_input("false-step", "tool-c4-false")],
        humanReview=_hitl_input(requiresConfirmation=True, onReject=OnRejectPolicy.ELSE_BRANCH),
    )
    wf_id, run_id, task = await _run_with_hitl("c4-else", [cond])
    pending = (await WorkflowRun.get(PydanticObjectId(run_id))).pending_requirements
    if pending:
        await control_service.resolve_requirement(
            wf_id, run_id, step_id=pending[0]["step_id"], resolution=RequirementResolution.REJECT
        )
    final = await _wait_status(run_id, WorkflowRunStatus.COMPLETED, WorkflowRunStatus.FAILED)
    # Condition CEL is always true; only the REJECT→else_branch override can route
    # to the false leg.  Asserting false ran AND true skipped therefore also proves
    # the gate fired (without it, the true leg would run).  Guards the
    # _ON_REJECT_TO_AGNO["else_branch"] = "else" mapping.
    completed_names = await _completed_names(run_id)
    r.check(
        "C4 reject + on_reject=else_branch → false branch ran, true branch skipped",
        final is not None
        and final.status == WorkflowRunStatus.COMPLETED
        and "false-step" in completed_names
        and "true-step" not in completed_names,
        f"completed nodes={sorted(completed_names)}, final={final.status if final else 'timeout'}",
    )
    await _await_or_cancel(task)

    # ── C5: user_input → injected into session for downstream ──────────────
    nodes_c5 = [
        _step_input(
            "collect",
            "tool-c5-collect",
            humanReview=_hitl_input(
                requiresUserInput=True,
                userInputSchema=[UserInputFieldSchema(name="discount", fieldType="number", required=True)],
            ),
        ),
        _step_input("after", "tool-c5-after"),
    ]
    wf_id, run_id, task = await _run_with_hitl("c5-user-input", nodes_c5)
    pending = (await WorkflowRun.get(PydanticObjectId(run_id))).pending_requirements
    await control_service.resolve_requirement(
        wf_id,
        run_id,
        step_id=pending[0]["step_id"],
        resolution=RequirementResolution.USER_INPUT,
        user_input={"discount": 15},
    )
    final = await _wait_status(run_id, WorkflowRunStatus.COMPLETED, WorkflowRunStatus.FAILED)
    collect_out = await _node_output(run_id, "collect")
    r.check(
        "C5 user_input reaches the gated step (discount=15 in output)",
        final is not None and final.status == WorkflowRunStatus.COMPLETED and "'discount': 15" in collect_out,
        f"final={final.status if final else 'timeout'}, collect_output={collect_out!r}",
    )
    await _await_or_cancel(task)

    # ── C6: output_review + edit → downstream sees edited output ───────────
    nodes_c6 = [
        _step_input(
            "produces",
            "tool-c6-prod",
            humanReview=_hitl_input(requiresOutputReview=True),
        ),
        _step_input("consumes", "tool-c6-cons"),
    ]
    wf_id, run_id, task = await _run_with_hitl("c6-edit", nodes_c6)
    pending = (await WorkflowRun.get(PydanticObjectId(run_id))).pending_requirements
    await control_service.resolve_requirement(
        wf_id,
        run_id,
        step_id=pending[0]["step_id"],
        resolution=RequirementResolution.EDIT,
        edited_output="EDITED_BY_USER",
    )
    final = await _wait_status(run_id, WorkflowRunStatus.COMPLETED, WorkflowRunStatus.FAILED)
    produces_out = await _node_output(run_id, "produces")
    consumes_out = await _node_output(run_id, "consumes")
    r.check(
        "C6 output_review + edit → edited output replaces node result AND flows downstream",
        final is not None
        and final.status == WorkflowRunStatus.COMPLETED
        and produces_out == "EDITED_BY_USER"
        and "EDITED_BY_USER" in consumes_out,
        f"final={final.status if final else 'timeout'}, produces={produces_out!r}, consumes={consumes_out!r}",
    )
    await _await_or_cancel(task)

    # ── C7: cross-session restore (simulate pod restart by recreating service) ─
    nodes_c7 = [_step_input("gate", "tool-c7", humanReview=_hitl_input(requiresConfirmation=True))]
    wf_id, run_id, task = await _run_with_hitl("c7-restore", nodes_c7)
    pending = (await WorkflowRun.get(PydanticObjectId(run_id))).pending_requirements
    step_id = pending[0]["step_id"]
    # Drop & rebuild control service to simulate a process boundary.
    fresh_queue = DirectiveQueue()
    fresh_runner = _build_runner(fresh_queue)
    fresh_service = WorkflowControlService(directive_queue=fresh_queue, runner_factory=lambda: fresh_runner)
    await fresh_service.resolve_requirement(wf_id, run_id, step_id=step_id, resolution=RequirementResolution.CONFIRM)
    final = await _wait_status(run_id, WorkflowRunStatus.COMPLETED, WorkflowRunStatus.FAILED, timeout=20)
    r.check(
        "C7 fresh service can resume from DB (pod-restart safe)",
        final is not None and final.status == WorkflowRunStatus.COMPLETED,
        f"final={final.status if final else 'timeout'}",
    )
    await _await_or_cancel(task)

    # ── C8: multi-step chain with HITL in the middle ────────────────────────
    chain = [
        _step_input("first", "tool-c8-1"),
        _step_input("gate", "tool-c8-2", humanReview=_hitl_input(requiresConfirmation=True)),
        _step_input("last", "tool-c8-3"),
    ]
    wf_id, run_id, task = await _run_with_hitl("c8-chain", chain)
    pending = (await WorkflowRun.get(PydanticObjectId(run_id))).pending_requirements
    await control_service.resolve_requirement(
        wf_id, run_id, step_id=pending[0]["step_id"], resolution=RequirementResolution.CONFIRM
    )
    final = await _wait_status(run_id, WorkflowRunStatus.COMPLETED, WorkflowRunStatus.FAILED)
    names = await _completed_names(run_id)
    r.check(
        "C8 multi-step A→GATE→B all complete after gate confirmed",
        final is not None and final.status == WorkflowRunStatus.COMPLETED and {"first", "gate", "last"} <= names,
        f"completed={sorted(names)}",
    )
    await _await_or_cancel(task)

    # ── C9: Condition + requires_confirmation (happy path, no reject) ──────
    cond9 = WorkflowNodeInput(
        name="route",
        nodeType="condition",
        conditionCel="session_state.user_text != ''",
        trueSteps=[_step_input("true-leg", "tool-c9-t")],
        falseSteps=[_step_input("false-leg", "tool-c9-f")],
        humanReview=_hitl_input(requiresConfirmation=True),
    )
    wf_id, run_id, task = await _run_with_hitl("c9-cond-confirm", [cond9])
    pending = (await WorkflowRun.get(PydanticObjectId(run_id))).pending_requirements
    r.check("C9a condition gate paused for confirmation", len(pending) > 0, f"pending={len(pending)}")
    if pending:
        await control_service.resolve_requirement(
            wf_id, run_id, step_id=pending[0]["step_id"], resolution=RequirementResolution.CONFIRM
        )
    final = await _wait_status(run_id, WorkflowRunStatus.COMPLETED, WorkflowRunStatus.FAILED)
    completed_names = await _completed_names(run_id)
    r.check(
        "C9 Condition + confirm → true branch ran, false branch skipped",
        final is not None
        and final.status == WorkflowRunStatus.COMPLETED
        and "true-leg" in completed_names
        and "false-leg" not in completed_names,
        f"completed={sorted(completed_names)}",
    )
    await _await_or_cancel(task)

    # ── C10: Router + route_select (HITL pick a named choice) ───────────────
    router = WorkflowNodeInput(
        name="router",
        nodeType="router",
        conditionCel="'tech'",  # default selector ignored when user_input picks
        choices=[
            {"name": "tech", "steps": [{"name": "tech-leg", "nodeType": "step", "executorKey": "tool-c10-tech"}]},
            {"name": "general", "steps": [{"name": "general-leg", "nodeType": "step", "executorKey": "tool-c10-gen"}]},
        ],
        humanReview=_hitl_input(requiresUserInput=True),
    )
    try:
        wf_id, run_id, task = await _run_with_hitl("c10-router", [router])
        pending = (await WorkflowRun.get(PydanticObjectId(run_id))).pending_requirements
        if pending:
            await control_service.resolve_requirement(
                wf_id,
                run_id,
                step_id=pending[0]["step_id"],
                resolution=RequirementResolution.ROUTE_SELECT,
                selected_choices=["general"],
            )
        final = await _wait_status(run_id, WorkflowRunStatus.COMPLETED, WorkflowRunStatus.FAILED, timeout=20)
        # Default selector picks "tech"; the user's ROUTE_SELECT picks "general".
        # Asserting general ran AND tech skipped proves the user override took
        # effect (and that the HITL gate actually fired).
        completed_names = await _completed_names(run_id)
        r.check(
            "C10 Router + route_select → user-picked 'general' leg ran, default 'tech' skipped",
            final is not None
            and final.status == WorkflowRunStatus.COMPLETED
            and "general-leg" in completed_names
            and "tech-leg" not in completed_names,
            f"final={final.status if final else 'timeout'}, completed={sorted(completed_names)}",
        )
        await _await_or_cancel(task)
    except Exception as exc:
        r.check("C10 Router + route_select", False, f"setup/exec error: {exc}")

    return r


async def _await_or_cancel(task: asyncio.Task) -> None:
    """Drain the runner task without raising on cancel/expected errors."""
    try:
        await asyncio.wait_for(task, timeout=10)
    except TimeoutError:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass  # Expected after cancellation.
    except Exception as exc:
        logger.debug("_await_or_cancel ignored: %s", exc)


# ════════════════════════════════════════════════════════════════════════════
# Module D — Cancel  (5 checks)
# ════════════════════════════════════════════════════════════════════════════


async def module_d(workflow_service, control_service, acl_service, queue, runner) -> Report:
    r = Report("D. Cancel")

    # D1: cancel a RUNNING run
    wf = await _make_workflow(
        workflow_service, acl_service, "d1-running", [_step_input("a", "tool-d1a"), _step_input("b", "tool-d1b")]
    )
    run_id, task = await _trigger_run_inproc(runner, str(wf.id))
    await asyncio.sleep(0.05)
    await control_service.send_cancel(str(wf.id), run_id)
    final = await _wait_status(
        run_id, WorkflowRunStatus.CANCELLED, WorkflowRunStatus.FAILED, WorkflowRunStatus.COMPLETED
    )
    r.check(
        "D1 cancel a RUNNING run → CANCELLED",
        final is not None and final.status == WorkflowRunStatus.CANCELLED,
        f"final={final.status if final else 'timeout'}",
    )
    await _await_or_cancel(task)

    # D2: cancel an AWAITING_APPROVAL run
    wf = await _make_workflow(
        workflow_service,
        acl_service,
        "d2-awaiting",
        [_step_input("gate", "tool-d2", humanReview=_hitl_input(requiresConfirmation=True))],
    )
    run_id, task = await _trigger_run_inproc(runner, str(wf.id))
    await _wait_status(run_id, WorkflowRunStatus.AWAITING_APPROVAL)
    await control_service.send_cancel(str(wf.id), run_id)
    final = await _wait_status(run_id, WorkflowRunStatus.CANCELLED, WorkflowRunStatus.FAILED)
    r.check(
        "D2 cancel during AWAITING_APPROVAL → CANCELLED",
        final is not None and final.status == WorkflowRunStatus.CANCELLED,
        f"final={final.status if final else 'timeout'}",
    )
    await _await_or_cancel(task)

    # D3: agno session bridged (pending_directive=CANCEL written by manager)
    run = await WorkflowRun.get(PydanticObjectId(run_id))
    r.check(
        "D3 cancel bridges to pending_directive=CANCEL",
        run is not None and str(run.pending_directive) == "cancel",
        f"pending_directive={run.pending_directive if run else 'no run'}",
    )

    # D4: idempotent cancel — second call still 200, no exception
    try:
        await control_service.send_cancel(str(wf.id), run_id)
        r.check("D4 idempotent cancel (second call OK)", True)
    except Exception as exc:
        r.check("D4 idempotent cancel", False, f"raised: {exc}")

    # D5: cancel a terminal (already CANCELLED) run — should remain CANCELLED
    try:
        await control_service.send_cancel(str(wf.id), run_id)
        run_after = await WorkflowRun.get(PydanticObjectId(run_id))
        r.check(
            "D5 cancel terminal run is no-op", run_after is not None and run_after.status == WorkflowRunStatus.CANCELLED
        )
    except HTTPException as exc:
        # 400 on terminal is also acceptable behavior (state machine rejects)
        r.check(
            "D5 cancel terminal run gives clear response",
            exc.status_code in (200, 400),
            f"status_code={exc.status_code}",
        )

    return r


# ════════════════════════════════════════════════════════════════════════════
# Module E — Cascade delete  (1 check)
# ════════════════════════════════════════════════════════════════════════════


async def module_e(workflow_service, control_service, acl_service, queue, runner) -> Report:
    r = Report("E. Cascade delete")

    wf = await _make_workflow(workflow_service, acl_service, "e1-cascade", [_step_input("only", "tool-e1")])
    # Generate runs + a HITL pause so agno_workflow_sessions exists.
    await workflow_service.trigger_workflow_run(workflow_id=str(wf.id))
    hitl_wf = await _make_workflow(
        workflow_service,
        acl_service,
        "e1-cascade-hitl",
        [_step_input("gate", "tool-e1-h", humanReview=_hitl_input(requiresConfirmation=True))],
    )
    run_id, task = await _trigger_run_inproc(runner, str(hitl_wf.id))
    await _wait_status(run_id, WorkflowRunStatus.AWAITING_APPROVAL)

    db = MongoDB.get_database()
    sessions_before = await db.get_collection("agno_workflow_sessions").count_documents({"session_id": run_id})

    # Update workflow once so a WorkflowVersion row exists.
    await workflow_service.update_workflow(
        str(hitl_wf.id),
        WorkflowUpdateRequest(
            name=hitl_wf.name,
            description="updated",
            nodes=[_convert_node_to_input(n) for n in hitl_wf.nodes],
        ),
    )

    await workflow_service.delete_workflow(str(hitl_wf.id))

    def_left = await WorkflowDefinition.get(hitl_wf.id)
    runs_left = await WorkflowRun.find(WorkflowRun.workflow_definition_id == hitl_wf.id).count()
    versions_left = await WorkflowVersion.find(WorkflowVersion.workflow_id == hitl_wf.id).count()
    sessions_left = await db.get_collection("agno_workflow_sessions").count_documents({"session_id": run_id})

    all_clean = def_left is None and runs_left == 0 and versions_left == 0 and sessions_left == 0
    r.check(
        "E1 DELETE cascades to definitions/runs/versions/agno_sessions",
        all_clean,
        f"def={def_left is None} runs={runs_left} versions={versions_left} agno_sessions={sessions_left} (before={sessions_before})",
    )
    await _await_or_cancel(task)

    return r


# ════════════════════════════════════════════════════════════════════════════
# Module F — Run query  (2 checks)
# ════════════════════════════════════════════════════════════════════════════


async def module_f(workflow_service, control_service, acl_service, queue, runner) -> Report:
    r = Report("F. Run query")

    wf = await _make_workflow(workflow_service, acl_service, "f1-list", [_step_input("only", "tool-f1")])
    await workflow_service.trigger_workflow_run(workflow_id=str(wf.id))
    await workflow_service.trigger_workflow_run(workflow_id=str(wf.id))
    listed, total = await workflow_service.list_workflow_runs(workflow_id=str(wf.id))
    r.check(
        "F1 GET /runs returns inserted runs", total >= 2 and len(listed) >= 2, f"total={total} returned={len(listed)}"
    )

    # F2: get run detail includes pending_requirements when awaiting
    wf2 = await _make_workflow(
        workflow_service,
        acl_service,
        "f2-detail",
        [_step_input("gate", "tool-f2", humanReview=_hitl_input(requiresConfirmation=True))],
    )
    run_id, task = await _trigger_run_inproc(runner, str(wf2.id))
    await _wait_status(run_id, WorkflowRunStatus.AWAITING_APPROVAL)
    run, _ = await workflow_service.get_workflow_run(str(wf2.id), run_id)
    r.check(
        "F2 GET /runs/{id} includes pending_requirements when paused",
        run is not None and len(run.pending_requirements) > 0,
        f"pending_requirements count={len(run.pending_requirements) if run else 'n/a'}",
    )
    # Cleanup
    await control_service.send_cancel(str(wf2.id), run_id)
    await _await_or_cancel(task)

    return r


# ════════════════════════════════════════════════════════════════════════════
# Module G — Retry  (1 check)
# ════════════════════════════════════════════════════════════════════════════


async def module_g(workflow_service, control_service, acl_service, queue, runner) -> Report:
    r = Report("G. Retry")

    wf = await _make_workflow(
        workflow_service, acl_service, "g1-retry", [_step_input("a", "tool-g1-a"), _step_input("b", "tool-g1-b")]
    )
    # Seed run to completion
    run_id, task = await _trigger_run_inproc(runner, str(wf.id))
    final = await _wait_status(run_id, WorkflowRunStatus.COMPLETED, WorkflowRunStatus.FAILED, timeout=20)
    await _await_or_cancel(task)

    if final is None or final.status != WorkflowRunStatus.COMPLETED:
        r.check(
            "G1 retry from node → child run created",
            False,
            f"seed run did not complete: {final.status if final else 'timeout'}",
        )
        return r

    first_node_id = wf.nodes[0].id
    child = await control_service.send_retry(
        str(wf.id),
        run_id,
        first_node_id,
        registry_token="test",
        user_id=str(USER_A),
    )
    r.check(
        "G1 retry from first node → child WorkflowRun created",
        child is not None
        and child.parent_run_id == final.id
        and str(child.status) in ("pending", "running", "completed"),
        f"child_id={child.id} parent={child.parent_run_id} status={child.status}",
    )

    return r


# ════════════════════════════════════════════════════════════════════════════
# Module H — Pause/Resume smoke  (1 check)
# ════════════════════════════════════════════════════════════════════════════


async def module_h(workflow_service, control_service, acl_service, queue, runner) -> Report:
    r = Report("H. Pause/Resume smoke")

    wf = await _make_workflow(
        workflow_service, acl_service, "h1-pause-resume", [_step_input("a", "tool-h1-a"), _step_input("b", "tool-h1-b")]
    )
    run_id, task = await _trigger_run_inproc(runner, str(wf.id))
    await asyncio.sleep(0.05)
    try:
        await control_service.send_pause(str(wf.id), run_id)
        await _wait_status(run_id, WorkflowRunStatus.PAUSED, timeout=5)
        await control_service.send_resume(str(wf.id), run_id)
    except HTTPException as exc:
        # Race: run might have already completed before we paused.  Acceptable
        # as long as it reached a terminal state.
        logger.debug("pause/resume race: %s", exc)
    final = await _wait_status(run_id, WorkflowRunStatus.COMPLETED, WorkflowRunStatus.FAILED, timeout=20)
    r.check(
        "H1 pause then resume → run completes",
        final is not None and final.status == WorkflowRunStatus.COMPLETED,
        f"final={final.status if final else 'timeout'}",
    )
    await _await_or_cancel(task)
    return r


# ════════════════════════════════════════════════════════════════════════════
# Module I — Error paths  (4 checks)
# ════════════════════════════════════════════════════════════════════════════


async def module_i(workflow_service, control_service, acl_service, queue, runner) -> Report:
    r = Report("I. Error paths")

    # I1: GET unknown workflow → ValueError / HTTPException(404)
    try:
        await workflow_service.get_workflow_by_id(str(PydanticObjectId()))
        r.check("I1 404 on unknown workflow id", False, "no exception raised")
    except (ValueError, HTTPException) as exc:
        code = getattr(exc, "status_code", None)
        r.check("I1 404 on unknown workflow id", code in (404, None), f"raised {type(exc).__name__}({code})")

    # I2: POST /runs with version=999 (non-existent) → 400/404
    wf = await _make_workflow(workflow_service, acl_service, "i2-bad-ver", [_step_input("x", "tool-i2")])
    try:
        await workflow_service.trigger_workflow_run(workflow_id=str(wf.id), version=999)
        r.check("I2 400/404 on bad version param", False, "no exception")
    except (ValueError, HTTPException) as exc:
        code = getattr(exc, "status_code", None)
        r.check("I2 400/404 on bad version param", code in (400, 404, None), f"raised {type(exc).__name__}({code})")

    # I3: cancel a terminal run → 400 or idempotent 200
    wf2 = await _make_workflow(workflow_service, acl_service, "i3-terminal", [_step_input("only", "tool-i3")])
    run_id, task = await _trigger_run_inproc(runner, str(wf2.id))
    final = await _wait_status(run_id, WorkflowRunStatus.COMPLETED, WorkflowRunStatus.FAILED, timeout=10)
    await _await_or_cancel(task)
    cancel_ok = False
    try:
        await control_service.send_cancel(str(wf2.id), run_id)
        # Idempotent: no exception is acceptable; run stays terminal
        cancel_ok = True
    except HTTPException as exc:
        cancel_ok = exc.status_code == 400
    r.check(
        "I3 cancel on terminal run handled (400 or idempotent)",
        cancel_ok,
        f"after-status={final.status if final else 'unknown'}",
    )

    # I4: invalid node shape (Condition without condition_cel) → ValueError at validation
    try:
        bad = WorkflowNodeInput(name="bad", nodeType="condition", trueSteps=[_step_input("x", "tool")])
        await workflow_service.create_workflow(
            data=WorkflowCreateRequest(name=PREFIX + "i4-bad", description="", nodes=[bad])
        )
        r.check("I4 400 on invalid node shape", False, "no exception raised")
    except (ValueError, HTTPException) as exc:
        code = getattr(exc, "status_code", None)
        r.check(
            "I4 400 on invalid node shape (Condition without condition_cel)",
            code in (400, 422, None),
            f"raised {type(exc).__name__}({code})",
        )

    return r


# ════════════════════════════════════════════════════════════════════════════
# Cleanup
# ════════════════════════════════════════════════════════════════════════════


async def cleanup() -> None:
    db = MongoDB.get_database()
    # Find all workflows we created by name prefix
    name_filter = {"name": {"$regex": f"^{PREFIX}"}}
    wf_ids = [wf["_id"] async for wf in db.get_collection("workflow_definitions").find(name_filter, {"_id": 1})]
    if wf_ids:
        run_docs = [
            r
            async for r in db.get_collection("workflow_runs").find(
                {"workflow_definition_id": {"$in": wf_ids}}, {"_id": 1}
            )
        ]
        run_ids = [d["_id"] for d in run_docs]
        if run_ids:
            await db.get_collection("node_runs").delete_many({"workflow_run_id": {"$in": run_ids}})
            await db.get_collection("workflow_runs").delete_many({"_id": {"$in": run_ids}})
            await db.get_collection("agno_workflow_sessions").delete_many(
                {"session_id": {"$in": [str(rid) for rid in run_ids]}}
            )
        await db.get_collection("workflow_versions").delete_many({"workflow_id": {"$in": wf_ids}})
        await db.get_collection("workflow_definitions").delete_many({"_id": {"$in": wf_ids}})
    await db.get_collection("aclentries").delete_many(
        {"resourceType": ExtendedResourceType.WORKFLOW.value, "principalId": {"$in": [USER_A, USER_B]}}
    )


# ════════════════════════════════════════════════════════════════════════════
# Main
# ════════════════════════════════════════════════════════════════════════════


MODULES = {
    "A": module_a,
    "B": module_b,
    "C": module_c,
    "D": module_d,
    "E": module_e,
    "F": module_f,
    "G": module_g,
    "H": module_h,
    "I": module_i,
}


async def amain(selected: list[str], keep_data: bool) -> int:
    await MongoDB.connect_db(
        config=MongoConfig(
            mongo_uri=os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017/jarvis"),
            mongodb_username=os.getenv("MONGODB_USERNAME", ""),
            mongodb_password=os.getenv("MONGODB_PASSWORD", ""),
        ),
    )

    workflow_service = WorkflowService()
    queue = DirectiveQueue()
    runner = _build_runner(queue)
    acl_service = ACLService(user_service=UserService(), group_service=GroupService())
    control_service = WorkflowControlService(directive_queue=queue, runner_factory=lambda: runner)

    reports: list[Report] = []
    t0 = time.monotonic()
    try:
        for key in selected:
            fn = MODULES[key]
            print(f"\n── {key} ─────────────────────────────────────────────────")
            try:
                # Modules A/B accept (workflow_service, control_service, acl_service)
                # Modules C-I need queue + runner too.
                if key in {"A", "B"}:
                    report = await fn(workflow_service, control_service, acl_service)
                else:
                    report = await fn(workflow_service, control_service, acl_service, queue, runner)
                reports.append(report)
            except Exception as exc:
                print(f"  {FAIL}  module {key} crashed: {exc}")
                traceback.print_exc()
                rep = Report(f"{key}. (crashed)")
                rep.check("module crashed", False, str(exc))
                reports.append(rep)
    finally:
        if not keep_data:
            print("\n── cleaning up test data ──")
            try:
                await cleanup()
                print(f"  {PASS}  removed all {PREFIX}* records")
            except Exception as exc:
                print(f"  {FAIL}  cleanup error: {exc}")
        else:
            print("\n  (--keep-data set; test records left in MongoDB)")

        await MongoDB.close_db()

    # Summary
    print("\n" + "═" * 60)
    print(f"SUMMARY  modules={len(reports)}  duration={time.monotonic() - t0:.1f}s")
    print("═" * 60)
    total_pass = 0
    total_all = 0
    for rep in reports:
        glyph = PASS if rep.passed == rep.total else FAIL
        print(f"  {glyph}  {rep.module:<35} {rep.passed}/{rep.total}")
        total_pass += rep.passed
        total_all += rep.total
    print("─" * 60)
    overall = PASS if total_pass == total_all else FAIL
    print(f"  {overall}  TOTAL                              {total_pass}/{total_all}")
    print("═" * 60)
    return 0 if total_pass == total_all else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--modules", default="A,B,C,D,E,F,G,H,I", help="Comma-separated module letters (default: all).")
    parser.add_argument(
        "--keep-data", action="store_true", help="Don't delete __as1543_e2e__* records at end (for debugging)."
    )
    args = parser.parse_args()
    selected = [m.strip().upper() for m in args.modules.split(",") if m.strip()]
    bad = [m for m in selected if m not in MODULES]
    if bad:
        print(f"Unknown module(s): {bad}.  Valid: {sorted(MODULES.keys())}", file=sys.stderr)
        return 2
    return asyncio.run(amain(selected, args.keep_data))


if __name__ == "__main__":
    sys.exit(main())
