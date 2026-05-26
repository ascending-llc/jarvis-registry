from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from agno.workflow import (
    Condition,
    Loop,
    Parallel,
    Router,
    Step,
    StepInput,
    StepOutput,
    Steps,
    Workflow,
)
from agno.workflow.step import OnError, StepExecutor
from agno.workflow.types import HumanReview

from registry_pkgs.models.enums import OnRejectPolicy, OnTimeoutPolicy, WorkflowNodeType
from registry_pkgs.models.workflow import (
    HumanReviewSpec,
    StepConfig,
    WorkflowDefinition,
    WorkflowNode,
    WorkflowRun,
)

# Anti-corruption layer: our enum values are the stable API/DB contract; agno's
# internal string values are an implementation detail we translate at the boundary.
# In particular agno spells ELSE_BRANCH as "else", not "else_branch".
_ON_REJECT_TO_AGNO: dict[OnRejectPolicy, str] = {
    OnRejectPolicy.SKIP: "skip",
    OnRejectPolicy.CANCEL: "cancel",
    OnRejectPolicy.RETRY: "retry",
    OnRejectPolicy.ELSE_BRANCH: "else",
}
_ON_TIMEOUT_TO_AGNO: dict[OnTimeoutPolicy, str] = {
    OnTimeoutPolicy.APPROVE: "approve",
    OnTimeoutPolicy.SKIP: "skip",
    OnTimeoutPolicy.CANCEL: "cancel",
}
from registry_pkgs.workflows.hitl.field_types import field_type_to_agno
from registry_pkgs.workflows.persistence import WorkflowRunSyncer
from registry_pkgs.workflows.types import POOL_KEY_PREFIX

if TYPE_CHECKING:
    from registry_pkgs.workflows.control import DirectiveQueue

logger = logging.getLogger(__name__)


def _to_agno_human_review(spec: HumanReviewSpec | None) -> HumanReview | None:
    """Translate our ``HumanReviewSpec`` into agno ``HumanReview``.

    Returns ``None`` when no HITL is configured so we don't pass an empty
    HumanReview into agno (which would still be a no-op but produces noisier
    serialized state).
    """
    if not spec:
        return None
    schema = (
        [{**f.model_dump(), "field_type": field_type_to_agno(f.field_type)} for f in spec.user_input_schema]
        if spec.user_input_schema
        else None
    )
    return HumanReview(
        requires_confirmation=spec.requires_confirmation,
        confirmation_message=spec.confirmation_message,
        requires_user_input=spec.requires_user_input,
        user_input_message=spec.user_input_message,
        user_input_schema=schema,
        requires_output_review=spec.requires_output_review,
        output_review_message=spec.output_review_message,
        requires_iteration_review=spec.requires_iteration_review,
        iteration_review_message=spec.iteration_review_message,
        on_reject=_ON_REJECT_TO_AGNO[spec.on_reject],
        timeout=spec.timeout_seconds,
        on_timeout=_ON_TIMEOUT_TO_AGNO[spec.on_timeout],
    )


def compile_workflow(
    definition: WorkflowDefinition,
    run: WorkflowRun,
    *,
    executor_registry: dict[str, StepExecutor],
    db_client: Any | None = None,
    db_name: str | None = None,
    directive_queue: DirectiveQueue | None = None,
    injected_outputs: dict[str, dict[str, Any]] | None = None,
) -> Workflow:
    """Compile a WorkflowDefinition + WorkflowRun into an agno Workflow.

    Args:
        definition:        The workflow definition loaded from MongoDB.
        run:               The WorkflowRun document (already inserted).
        executor_registry: Maps executor_key strings to async executor functions.
        db_client:         pymongo AsyncMongoClient.  When provided (with db_name),
                           a WorkflowRunSyncer is attached so agno's upsert_session
                           automatically syncs run state to WorkflowRun / NodeRun.
        db_name:           MongoDB database name (required when db_client is set).
        directive_queue:   When provided, every STEP executor is wrapped with
                           :func:`~registry_pkgs.workflows.control.with_control`
                           to enable pause, cancel, and retry-backoff behaviour.
        injected_outputs:  Mapping of ``node_id → output content`` for nodes that
                           should be skipped by replaying a cached result.  Used
                           when retrying a run from a specific node so that
                           previously completed nodes are not re-executed.
    """
    if (db_client is None) != (db_name is None):
        raise ValueError("compile_workflow requires db_client and db_name together")

    db: WorkflowRunSyncer | None = None
    if db_client is not None and db_name is not None:
        node_by_name = {n.name: n for n in flatten_workflow_nodes(definition.nodes)}
        db = WorkflowRunSyncer(
            workflow_run=run,
            node_by_name=node_by_name,
            db_client=db_client,
            db_name=db_name,
        )

    _injected = injected_outputs or {}

    def _make_injected_executor(data: dict[str, Any]) -> StepExecutor:
        """Return a pass-through executor that replays *content* and *session_state*."""

        async def _injected(step_input: StepInput, session_state: dict | None = None) -> StepOutput:
            if session_state is not None:
                state_updates = data.get("session_state")
                if state_updates:
                    session_state.update(state_updates)
            return StepOutput(content=data.get("content"), success=True)

        return _injected

    def _build(node: WorkflowNode) -> Any:

        human_review = _to_agno_human_review(node.human_review)
        if node.node_type == WorkflowNodeType.STEP:
            lookup_key = f"{POOL_KEY_PREFIX}{node.id}" if node.a2a_pool else node.executor_key

            # Nodes whose output was produced by a previous run are replaced with
            # a lightweight pass-through executor so they complete instantly without
            # calling the underlying MCP / A2A service again.
            if node.id in _injected:
                executor: StepExecutor = _make_injected_executor(_injected[node.id])
            else:
                executor = executor_registry.get(lookup_key)  # type: ignore[arg-type]
                if executor is None:
                    raise KeyError(
                        f"executor key {lookup_key!r} not found in executor_registry "
                        f"(registered: {list(executor_registry)})"
                    )

            # Wrap with directive checking and retry backoff when a queue is present.
            if directive_queue is not None:
                from registry_pkgs.workflows.control import with_control

                executor = with_control(
                    executor,
                    run_id=str(run.id),
                    node_id=node.id,
                    node_name=node.name,
                    step_config=node.step_config,
                    directive_queue=directive_queue,
                )
            return Step(
                name=node.name,
                executor=executor,
                human_review=human_review,
                **step_kwargs(node.step_config),
            )

        if node.node_type == WorkflowNodeType.PARALLEL:
            # agno's Parallel does not accept human_review (model layer enforces this too).
            return Parallel(*[_build(c) for c in node.children], name=node.name)

        if node.node_type == WorkflowNodeType.CONDITION:
            true_branch = [_build(c) for c in node.true_steps]
            false_branch = [_build(c) for c in node.false_steps] or None
            return Condition(
                steps=true_branch,
                evaluator=node.condition_cel,
                else_steps=false_branch,
                name=node.name,
                human_review=human_review,
            )

        if node.node_type == WorkflowNodeType.LOOP:
            end_cond = node.loop_config.end_condition_cel if node.loop_config else None  # type: ignore[union-attr]
            return Loop(
                steps=[_build(c) for c in node.children],
                name=node.name,
                max_iterations=node.loop_config.max_iterations,  # type: ignore[union-attr]
                end_condition=end_cond,
                human_review=human_review,
            )

        if node.node_type == WorkflowNodeType.ROUTER:
            compiled_choices = [
                Steps(name=choice.name, steps=[_build(s) for s in choice.steps]) for choice in node.choices
            ]
            return Router(
                choices=compiled_choices,
                selector=node.condition_cel,
                name=node.name,
                human_review=human_review,
            )

        raise ValueError(f"Unknown node_type: {node.node_type!r}")

    steps = [_build(n) for n in definition.nodes]
    workflow_kwargs: dict[str, Any] = {
        "id": str(definition.id),
        "name": definition.name,
        "steps": steps,
    }
    if db is not None:
        workflow_kwargs["db"] = db
    return Workflow(**workflow_kwargs)


def step_kwargs(cfg: StepConfig | None) -> dict[str, Any]:
    """Translate a StepConfig into agno Step keyword arguments.

    When ``cfg`` is ``None`` the step runs with safe production defaults:
    no retries, fail-fast on error.

    When ``on_error == "retry"`` the retry loop is fully managed by the
    ``with_control`` wrapper, so agno receives ``max_retries=0`` and
    ``on_error=fail`` — agno must not interfere with our backoff logic.
    """
    if cfg is None:
        return {"max_retries": 0, "skip_on_failure": False, "on_error": OnError.fail}
    if cfg.on_error == "retry":
        # Retries are handled by the executor wrapper; tell agno to treat a
        # (wrapper-level) failure as a normal step failure without extra retries.
        return {"max_retries": 0, "skip_on_failure": False, "on_error": OnError.fail}
    return {
        "max_retries": cfg.max_retries,
        "skip_on_failure": cfg.on_error == "skip",
        "on_error": OnError.skip if cfg.on_error == "skip" else OnError.fail,
    }


def flatten_workflow_nodes(nodes: list[WorkflowNode]) -> list[WorkflowNode]:
    """Recursively collect every WorkflowNode in the tree.

    Covers all container shapes: ``children`` (PARALLEL / LOOP),
    ``true_steps`` / ``false_steps`` (CONDITION), and ``choices[*].steps``
    (ROUTER). ``RouterChoice`` itself is not a ``WorkflowNode`` and is not
    included; only the inner step nodes are.
    """
    result: list[WorkflowNode] = []
    for node in nodes:
        result.append(node)
        if node.children:
            result.extend(flatten_workflow_nodes(node.children))
        if node.true_steps:
            result.extend(flatten_workflow_nodes(node.true_steps))
        if node.false_steps:
            result.extend(flatten_workflow_nodes(node.false_steps))
        for choice in node.choices:
            result.extend(flatten_workflow_nodes(choice.steps))
    return result
