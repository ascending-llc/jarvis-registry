from __future__ import annotations

import copy
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
from registry_pkgs.workflows.hitl.field_types import field_type_to_agno
from registry_pkgs.workflows.media_snapshot import (
    media_from_snapshot,
    serialize_media_items,
    serialize_step_output_media,
)
from registry_pkgs.workflows.persistence import WorkflowRunSyncer
from registry_pkgs.workflows.prompt import (
    ADDITIONAL_DATA_DEPENDENCY_NODE_NAMES,
    ADDITIONAL_DATA_DEPENDENCY_OBJECTIVES,
    ADDITIONAL_DATA_STEP_OBJECTIVE,
    ADDITIONAL_DATA_WORKFLOW_DESCRIPTION,
)
from registry_pkgs.workflows.serialization import json_safe, try_parse_json
from registry_pkgs.workflows.types import NODE_INPUT_SNAPSHOTS_KEY, POOL_KEY_PREFIX

if TYPE_CHECKING:
    from registry_pkgs.workflows.control import DirectiveQueue

logger = logging.getLogger(__name__)

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


def _serialize_step_output(value: StepOutput) -> dict[str, Any]:
    """Serialize a previous StepOutput without recursively storing full internals.

    Media fields are persisted as metadata only (no bytes) via
    ``serialize_step_output_media`` so snapshots stay Mongo-safe and small.
    """
    serialized = {
        "step_name": value.step_name,
        "step_id": value.step_id,
        "content": json_safe(value.content),
        "success": value.success,
        "error": value.error,
    }
    serialized.update(serialize_step_output_media(value))
    return serialized


def _serialize_step_input(step_input: StepInput) -> dict[str, Any]:
    """Serialize the fields users need to debug how a node was invoked.

    Captures the pre-injection StepInput (before _with_intention_data enriches
    additional_data) so the snapshot reflects the raw agno-level input, not the
    rendered prompt.
    """
    previous_outputs = step_input.previous_step_outputs or {}
    return {
        "input": json_safe(try_parse_json(step_input.input)),
        "previous_step_content": json_safe(step_input.previous_step_content),
        "previous_step_outputs": {
            str(name): _serialize_step_output(output) for name, output in previous_outputs.items()
        },
        "additional_data": json_safe(step_input.additional_data),
        "images": serialize_media_items(step_input.images, "images"),
        "videos": serialize_media_items(step_input.videos, "videos"),
        "audio": serialize_media_items(step_input.audio, "audio"),
        "files": serialize_media_items(step_input.files, "files"),
    }


def _dedupe_preserve_order(names: list[str]) -> list[str]:
    """Dedupe while keeping first-occurrence order (implicit dep stays ahead of explicit refs)."""
    return list(dict.fromkeys(names))


def _build_implicit_previous_step_names(nodes: list[WorkflowNode]) -> dict[str, str]:
    """Map each STEP node id to the immediately previous STEP in the same ordered list.

    Branch containers (Loop / Condition / Router) form independent chains inside
    each branch; Parallel siblings never depend on each other; a container node
    breaks the chain (a STEP never implicitly depends on a container).
    """
    implicit_by_node_id: dict[str, str] = {}

    def visit_sequence(sequence: list[WorkflowNode]) -> None:
        """Walk one ordered node list, linking STEP→previous-STEP and recursing into containers."""
        previous_node: WorkflowNode | None = None
        for node in sequence:
            if node.node_type == WorkflowNodeType.STEP:
                if previous_node is not None and previous_node.node_type == WorkflowNodeType.STEP:
                    implicit_by_node_id[node.id] = previous_node.name

            if node.node_type == WorkflowNodeType.LOOP:
                visit_sequence(node.children)
            elif node.node_type == WorkflowNodeType.CONDITION:
                visit_sequence(node.true_steps)
                visit_sequence(node.false_steps)
            elif node.node_type == WorkflowNodeType.ROUTER:
                for choice in node.choices:
                    visit_sequence(choice.steps)
            elif node.node_type == WorkflowNodeType.PARALLEL:
                # Parallel children have no ordering semantics, so do not create
                # implicit dependencies between siblings.
                for child in node.children:
                    visit_sequence([child])
            previous_node = node

    visit_sequence(nodes)
    return implicit_by_node_id


def _with_input_capture(
    node: WorkflowNode,
    executor: StepExecutor,
) -> StepExecutor:
    """Wrap a step executor so NodeRun.input_snapshot can be persisted later."""

    async def _capturing(step_input: StepInput, session_state: dict | None = None) -> StepOutput:
        if session_state is not None:
            snapshots = session_state.setdefault(NODE_INPUT_SNAPSHOTS_KEY, {})
            snapshots[node.id] = _serialize_step_input(step_input)
        return await executor(step_input, session_state)

    return _capturing


def _with_intention_data(
    node: WorkflowNode,
    executor: StepExecutor,
    node_by_name: dict[str, WorkflowNode],
    workflow_description: str | None,
    dependency_node_names: list[str],
) -> StepExecutor:
    """Inject per-node intention into ``StepInput.additional_data`` before calling the executor.

    Constructed fresh per ``WorkflowNode`` in ``_build`` so per-node objective and
    dependency data can never leak across nodes that share the same executor in
    ``executor_registry`` (two nodes with the same ``executorKey`` share one executor
    object, but each gets its own wrapper closure with its own captured variables).

    The wrapper uses ``copy.copy(step_input)`` so the original object is never
    mutated — ``_with_input_capture`` (outermost) therefore snapshots the
    pre-injection ``StepInput``, exactly preserving today's ``NodeRun.input_snapshot``
    semantics.

    Keys written to ``additional_data`` are the constants defined in ``prompt.py``
    (``ADDITIONAL_DATA_*``).  ``build_prompt`` in ``helpers.py`` reads them back
    and calls ``render_step_prompt`` to assemble the final Markdown prompt.
    """
    dependency_objectives: dict[str, str] = {
        name: node_by_name[name].step_objective or "" for name in dependency_node_names if name in node_by_name
    }

    async def _wrapped(step_input: StepInput, session_state: dict | None = None) -> StepOutput:
        enriched = copy.copy(step_input)
        enriched.additional_data = {
            **(step_input.additional_data or {}),
            ADDITIONAL_DATA_STEP_OBJECTIVE: node.step_objective or "",
            ADDITIONAL_DATA_WORKFLOW_DESCRIPTION: workflow_description,
            ADDITIONAL_DATA_DEPENDENCY_NODE_NAMES: dependency_node_names,
            ADDITIONAL_DATA_DEPENDENCY_OBJECTIVES: dependency_objectives,
        }
        return await executor(enriched, session_state)

    _wrapped.__name__ = f"intention_wrapper({node.name})"
    return _wrapped


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
    stop_after_node_id: str | None = None,
) -> Workflow:
    """Compile a WorkflowDefinition + WorkflowRun into an agno Workflow.

    Args:
        definition:          The workflow definition loaded from MongoDB.
        run:                 The WorkflowRun document (already inserted).
        executor_registry:   Maps executor_key strings to async executor functions.
        db_client:           pymongo AsyncMongoClient.  When provided (with db_name),
                             a WorkflowRunSyncer is attached so agno's upsert_session
                             automatically syncs run state to WorkflowRun / NodeRun.
        db_name:             MongoDB database name (required when db_client is set).
        directive_queue:     When provided, every STEP executor is wrapped with
                             :func:`~registry_pkgs.workflows.control.with_control`
                             to enable pause, cancel, and retry-backoff behaviour.
        injected_outputs:    Mapping of ``node_id → output content`` for nodes that
                             should be skipped by replaying a cached result.  Used
                             when retrying a run from a specific node so that
                             previously completed nodes are not re-executed.
        stop_after_node_id:  When set, only compile top-level nodes up to and including
                             this node ID.  Downstream nodes are excluded from the agno
                             Workflow entirely, so they produce no NodeRun records.
                             Only top-level (non-nested) nodes are supported.
    """
    if (db_client is None) != (db_name is None):
        raise ValueError("compile_workflow requires db_client and db_name together")

    # Build unconditionally — needed for dependency-goal resolution in
    # _with_intention_data regardless of whether DB sync is active.
    node_by_name: dict[str, WorkflowNode] = {n.name: n for n in flatten_workflow_nodes(definition.nodes)}
    implicit_previous_step_names = _build_implicit_previous_step_names(definition.nodes)

    db: WorkflowRunSyncer | None = None
    if db_client is not None and db_name is not None:
        db = WorkflowRunSyncer(
            workflow_run=run,
            node_by_name=node_by_name,
            db_client=db_client,
            db_name=db_name,
        )

    _injected = injected_outputs or {}

    nodes_to_compile = definition.nodes
    if stop_after_node_id is not None:
        top_level_ids = [n.id for n in definition.nodes]
        if stop_after_node_id not in top_level_ids:
            raise ValueError(
                f"stop_after_node_id {stop_after_node_id!r} not found in top-level nodes "
                f"(found: {top_level_ids}). Nested node rerun is not supported."
            )
        cut = top_level_ids.index(stop_after_node_id)
        nodes_to_compile = definition.nodes[: cut + 1]

    def _make_injected_executor(data: dict[str, Any]) -> StepExecutor:
        """Return a pass-through executor that replays *content*, *media metadata* and *session_state*.

        Media are rebuilt as metadata-only shells (no bytes) so downstream
        dependency prompts render the same media summary as a live run.
        """
        media = media_from_snapshot(data)

        async def _injected(step_input: StepInput, session_state: dict | None = None) -> StepOutput:
            if session_state is not None:
                state_updates = data.get("session_state")
                if state_updates:
                    session_state.update(state_updates)
            return StepOutput(
                content=data.get("content"),
                images=media["images"],
                videos=media["videos"],
                audio=media["audio"],
                files=media["files"],
                success=True,
            )

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
                # Live executors rely on build_prompt(), so inject per-node intention
                # into StepInput.additional_data before invoking the underlying executor.
                implicit_name = implicit_previous_step_names.get(node.id)
                dependency_node_names = _dedupe_preserve_order(
                    ([implicit_name] if implicit_name else []) + list(node.referenced_node_names)
                )
                executor = _with_intention_data(
                    node,
                    executor,
                    node_by_name,
                    definition.description,
                    dependency_node_names,
                )

            # _with_input_capture snapshots the pre-injection StepInput, so it
            # must be the outermost wrapper around both live and injected paths.
            executor = _with_input_capture(node, executor)

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

    steps = [_build(n) for n in nodes_to_compile]
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
