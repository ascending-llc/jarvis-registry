from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from agno.workflow import (
    Condition,
    Loop,
    Parallel,
    Router,
    Step,
    StepInput,
    StepOutput,
    Workflow,
)

from registry_pkgs.models.enums import WorkflowNodeType
from registry_pkgs.models.workflow import WorkflowDefinition, WorkflowNode, WorkflowRun
from registry_pkgs.workflows.persistence import WorkflowRunSync

WorkflowExecutor = Callable[[StepInput, dict[str, Any]], Awaitable[StepOutput]]

logger = logging.getLogger(__name__)


def compile_workflow(
    definition: WorkflowDefinition,
    run: WorkflowRun,
    *,
    executor_registry: dict[str, WorkflowExecutor],
    db_client: Any | None = None,
    db_name: str | None = None,
) -> Workflow:
    """Compile a WorkflowDefinition + WorkflowRun into an agno Workflow.

    Args:
        definition:        The workflow definition loaded from MongoDB.
        run:               The WorkflowRun document (already inserted).
        executor_registry: Maps executor_key strings to async executor functions.
        db_client:         pymongo AsyncMongoClient.  When provided (with db_name),
                           a WorkflowRunSync is attached so agno's upsert_session
                           automatically syncs run state to WorkflowRun / NodeRun.
        db_name:           MongoDB database name (required when db_client is set).
    """
    if (db_client is None) != (db_name is None):
        raise ValueError("compile_workflow requires db_client and db_name together")

    db: WorkflowRunSync | None = None
    if db_client is not None and db_name is not None:
        node_by_name = {n.name: n for n in flatten_workflow_nodes(definition.nodes)}
        db = WorkflowRunSync(
            workflow_run=run,
            node_by_name=node_by_name,
            db_client=db_client,
            db_name=db_name,
        )

    def _build(node: WorkflowNode) -> Any:
        if node.node_type == WorkflowNodeType.STEP:
            executor = executor_registry.get(node.executor_key)  # type: ignore[arg-type]
            if executor is None:
                raise KeyError(
                    f"executor_key {node.executor_key!r} not found in executor_registry "
                    f"(registered: {list(executor_registry)})"
                )
            return Step(
                name=node.name,
                executor=executor,
                max_retries=0,
                skip_on_failure=False,
            )

        if node.node_type == WorkflowNodeType.PARALLEL:
            return Parallel(*[_build(c) for c in node.children], name=node.name)

        if node.node_type == WorkflowNodeType.CONDITION:
            true_branch = _build(node.children[0])
            false_branch = _build(node.children[1]) if len(node.children) > 1 else None
            return Condition(
                steps=[true_branch],
                evaluator=node.condition_cel,
                else_steps=[false_branch] if false_branch is not None else None,
                name=node.name,
            )

        if node.node_type == WorkflowNodeType.LOOP:
            end_cond = node.loop_config.end_condition_cel if node.loop_config else None  # type: ignore[union-attr]
            return Loop(
                steps=[_build(c) for c in node.children],
                name=node.name,
                max_iterations=node.loop_config.max_iterations,  # type: ignore[union-attr]
                end_condition=end_cond,
            )

        if node.node_type == WorkflowNodeType.ROUTER:
            return Router(
                choices=[_build(c) for c in node.children],
                selector=node.condition_cel,
                name=node.name,
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


def flatten_workflow_nodes(nodes: list[WorkflowNode]) -> list[WorkflowNode]:
    """Recursively collect every node in the tree (including nested children)."""
    result: list[WorkflowNode] = []
    for node in nodes:
        result.append(node)
        if node.children:
            result.extend(flatten_workflow_nodes(node.children))
    return result
