"""
Workflow Executor - Background execution service for workflow runs

This module provides functions to execute workflows asynchronously using BackgroundTasks.
"""

import logging
from typing import Any

from beanie import PydanticObjectId

from registry_pkgs.models.enums import WorkflowRunStatus
from registry_pkgs.models.workflow import NodeRun, WorkflowRun

logger = logging.getLogger(__name__)


async def execute_workflow_run_background(
    run_id: str | PydanticObjectId,
    workflow_runner: Any,
    registry_token: str | None = None,
    user_id: str | None = None,
) -> None:
    """
    Execute a workflow run in the background.

    This function is designed to be called by FastAPI BackgroundTasks.
    It updates the WorkflowRun status as it progresses.

    Args:
        run_id: WorkflowRun ID to execute
        workflow_runner: WorkflowRunner instance
        registry_token: User's JWT token for authenticated calls
        user_id: User ID for ACL filtering (optional)
    """
    run_id_str = str(run_id)

    try:
        logger.info(f"Starting background execution for workflow run {run_id_str}")

        # Get the workflow run
        run = await WorkflowRun.get(PydanticObjectId(run_id))
        if not run:
            logger.error(f"Workflow run {run_id_str} not found")
            return

        # Check if runner is available
        if workflow_runner is None:
            logger.error(f"WorkflowRunner not available - cannot execute run {run_id_str}")
            run.status = WorkflowRunStatus.FAILED
            run.error_summary = "Workflow execution engine not available"
            await run.save()
            return

        # Extract user input
        user_text = ""
        if run.initial_input:
            # Try to extract user_text from initial_input
            user_text = run.initial_input.get("user_text", "")
            if not user_text and run.initial_input:
                # If no user_text, use the entire input as JSON string
                import json

                user_text = json.dumps(run.initial_input)

        # Get accessible agent IDs for ACL filtering (None = unrestricted for now)
        # TODO: In future, get this from ACL service based on user_id
        accessible_agent_ids = None

        logger.info(f"Starting execution for run {run_id_str} (workflow: {run.workflow_definition_id})")

        # Execute using WorkflowRunner
        logger.info(f"[Run {run_id_str}] Step 1: Building executor registry...")

        # The runner will:
        # 1. Load the WorkflowDefinition
        # 2. Build executor registry (MCP tools, A2A agents)
        # 3. Compile to agno Workflow
        # 4. Execute and update status via WorkflowRunSyncer
        # 5. Return the final run and node_runs
        updated_run, node_runs = await workflow_runner.run(
            definition_id=str(run.workflow_definition_id),
            user_text=user_text,
            registry_token=registry_token or "",
            accessible_agent_ids=accessible_agent_ids,
            trigger_source=run.trigger_source or "api",
        )

        logger.info(f"[Run {run_id_str}] Execution completed: status={updated_run.status}, node_runs={len(node_runs)}")

    except Exception as e:
        logger.error(f"Error executing workflow run {run_id_str}: {e}", exc_info=True)

        # Try to mark run as failed
        try:
            run = await WorkflowRun.get(PydanticObjectId(run_id))
            if run:
                run.status = WorkflowRunStatus.FAILED
                run.error_summary = f"Execution error: {str(e)}"
                from datetime import UTC, datetime

                run.finished_at = datetime.now(UTC)
                await run.save()
                logger.info(f"Marked workflow run {run_id_str} as FAILED")
        except Exception as save_error:
            logger.error(f"Failed to update run status: {save_error}", exc_info=True)


async def get_workflow_run_with_nodes(
    workflow_id: str | PydanticObjectId,
    run_id: str | PydanticObjectId,
) -> tuple[WorkflowRun, list[NodeRun]] | tuple[None, None]:
    """
    Get a workflow run with all its node runs.

    Args:
        workflow_id: Workflow definition ID
        run_id: Workflow run ID

    Returns:
        Tuple of (WorkflowRun, list of NodeRuns) or (None, None) if not found
    """
    try:
        # Get workflow run
        run = await WorkflowRun.get(PydanticObjectId(run_id))
        if not run:
            return None, None

        # Verify run belongs to workflow
        if str(run.workflow_definition_id) != str(workflow_id):
            return None, None

        # Get all node runs
        node_runs = await NodeRun.find(NodeRun.workflow_run_id == run.id).sort("started_at").to_list()

        return run, node_runs

    except Exception as e:
        logger.error(f"Error getting workflow run {run_id}: {e}", exc_info=True)
        return None, None
