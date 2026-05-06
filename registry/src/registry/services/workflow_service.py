"""
Workflow Service - Business logic for Workflow Management API

This service handles all workflow-related operations using MongoDB and Beanie ODM.
"""

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from beanie import PydanticObjectId

from registry_pkgs.models.enums import WorkflowRunStatus
from registry_pkgs.models.workflow import NodeRun, WorkflowDefinition, WorkflowNode, WorkflowRun

from ..schemas.workflow_api_schemas import WorkflowCreateRequest, WorkflowUpdateRequest

logger = logging.getLogger(__name__)


class WorkflowService:
    """Service for Workflow operations"""

    async def list_workflows(
        self,
        query: str | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[WorkflowDefinition], int]:
        """
        List workflows with optional filtering and pagination.

        Args:
            query: Free-text search across workflow name, description
            page: Page number (validated by router)
            per_page: Items per page (validated by router)

        Returns:
            Tuple of (workflows list, total count)
        """
        try:
            # Build query filters
            filters: dict[str, Any] = {}

            # Free-text search
            if query:
                search_pattern = {"$regex": query, "$options": "i"}
                filters["$or"] = [
                    {"name": search_pattern},
                    {"description": search_pattern},
                ]

            # Get total count
            total = await WorkflowDefinition.find(filters).count()

            # Get paginated results
            skip = (page - 1) * per_page
            workflows = await WorkflowDefinition.find(filters).sort("-created_at").skip(skip).limit(per_page).to_list()

            logger.info(f"Listed {len(workflows)} workflows (total: {total}, page: {page}, per_page: {per_page})")
            return workflows, total

        except Exception as e:
            logger.error(f"Error listing workflows: {e}", exc_info=True)
            raise

    async def get_workflow_by_id(self, workflow_id: str) -> WorkflowDefinition:
        """
        Get workflow by ID.

        Args:
            workflow_id: Workflow ID

        Returns:
            WorkflowDefinition document

        Raises:
            ValueError: If workflow not found or invalid ID
        """
        try:
            workflow = await WorkflowDefinition.get(PydanticObjectId(workflow_id))
            if not workflow:
                raise ValueError(f"Workflow {workflow_id} not found")

            logger.info(f"Retrieved workflow {workflow_id}: {workflow.name}")
            return workflow

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Error getting workflow {workflow_id}: {e}", exc_info=True)
            raise ValueError(f"Invalid workflow ID: {workflow_id}")

    async def create_workflow(
        self,
        data: WorkflowCreateRequest,
    ) -> WorkflowDefinition:
        """
        Create a new workflow.

        Args:
            data: Workflow creation request

        Returns:
            Created WorkflowDefinition document

        Raises:
            ValueError: If validation fails
        """
        try:
            # Convert API nodes to model nodes
            nodes = [self._convert_api_node_to_model(node) for node in data.nodes]

            # Create workflow definition
            workflow = WorkflowDefinition(
                name=data.name,
                description=data.description,
                nodes=nodes,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )

            # Save to database (this will trigger Pydantic validation)
            await workflow.insert()

            logger.info(f"Created workflow {workflow.id}: {workflow.name}")
            return workflow

        except ValueError as e:
            logger.error(f"Validation error creating workflow: {e}")
            raise
        except Exception as e:
            logger.error(f"Error creating workflow: {e}", exc_info=True)
            raise ValueError(f"Failed to create workflow: {e}")

    async def update_workflow(
        self,
        workflow_id: str,
        data: WorkflowUpdateRequest,
    ) -> WorkflowDefinition:
        """
        Update an existing workflow.

        Args:
            workflow_id: Workflow ID
            data: Workflow update request

        Returns:
            Updated WorkflowDefinition document

        Raises:
            ValueError: If workflow not found or validation fails
        """
        try:
            # Get existing workflow
            workflow = await self.get_workflow_by_id(workflow_id)

            # Update fields if provided
            update_data: dict[str, Any] = {}

            if data.name is not None:
                update_data["name"] = data.name

            if data.description is not None:
                update_data["description"] = data.description

            if data.nodes is not None:
                update_data["nodes"] = [self._convert_api_node_to_model(node) for node in data.nodes]

            # Always update the timestamp
            update_data["updated_at"] = datetime.now(UTC)

            # Apply updates
            for key, value in update_data.items():
                setattr(workflow, key, value)

            # Save to database (this will trigger Pydantic validation)
            await workflow.save()

            logger.info(f"Updated workflow {workflow_id}: {workflow.name}")
            return workflow

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Error updating workflow {workflow_id}: {e}", exc_info=True)
            raise ValueError(f"Failed to update workflow: {e}")

    async def delete_workflow(self, workflow_id: str) -> bool:
        """
        Delete a workflow and all associated runs.

        Args:
            workflow_id: Workflow ID

        Returns:
            True if deleted successfully

        Raises:
            ValueError: If workflow not found
        """
        try:
            # Get existing workflow
            workflow = await self.get_workflow_by_id(workflow_id)

            # Delete all associated workflow runs
            runs = await WorkflowRun.find(WorkflowRun.workflow_definition_id == workflow.id).to_list()
            run_ids = [run.id for run in runs]

            # Delete all node runs for these workflow runs
            if run_ids:
                await NodeRun.find({"workflow_run_id": {"$in": run_ids}}).delete()
                logger.info(f"Deleted node runs for {len(run_ids)} workflow runs")

            # Delete all workflow runs
            await WorkflowRun.find(WorkflowRun.workflow_definition_id == workflow.id).delete()
            logger.info(f"Deleted {len(runs)} workflow runs for workflow {workflow_id}")

            # Delete the workflow
            await workflow.delete()

            logger.info(f"Deleted workflow {workflow_id}: {workflow.name}")
            return True

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Error deleting workflow {workflow_id}: {e}", exc_info=True)
            raise ValueError(f"Failed to delete workflow: {e}")

    async def trigger_workflow_run(
        self,
        workflow_id: str,
        trigger_source: str | None = None,
        initial_input: dict[str, Any] | None = None,
        parent_run_id: str | None = None,
        resolved_dependencies: list[dict[str, Any]] | None = None,
    ) -> WorkflowRun:
        """
        Trigger a workflow run (async execution).

        Note: This method creates the WorkflowRun record with PENDING status.
        The actual execution should be handled by a background task/worker.

        Args:
            workflow_id: Workflow ID
            trigger_source: Source that triggered the run
            initial_input: Initial input data
            parent_run_id: Parent run ID for retry
            resolved_dependencies: Dependency resolution for retry

        Returns:
            Created WorkflowRun document (status=PENDING)

        Raises:
            ValueError: If workflow not found or validation fails
        """
        try:
            # Get existing workflow
            workflow = await self.get_workflow_by_id(workflow_id)

            # Convert resolved_dependencies if provided
            from registry_pkgs.models.workflow import ResolvedDependency

            resolved_deps = []
            if resolved_dependencies:
                for dep in resolved_dependencies:
                    resolved_deps.append(
                        ResolvedDependency(
                            node_id=dep["nodeId"],
                            resolution=dep["resolution"],
                            source_node_run_id=(
                                PydanticObjectId(dep["sourceNodeRunId"]) if dep.get("sourceNodeRunId") else None
                            ),
                        )
                    )

            # Create workflow run
            run = WorkflowRun(
                workflow_definition_id=workflow.id,
                status=WorkflowRunStatus.PENDING,
                trigger_source=trigger_source,
                started_at=datetime.now(UTC),
                initial_input=initial_input,
                definition_snapshot=workflow.model_dump(mode="json"),
                parent_run_id=PydanticObjectId(parent_run_id) if parent_run_id else None,
                resolved_dependencies=resolved_deps,
            )

            # Save to database
            await run.insert()

            logger.info(
                f"Triggered workflow run {run.id} for workflow {workflow_id} "
                f"(source: {trigger_source}, parent: {parent_run_id})"
            )

            # TODO: Enqueue background task to execute the workflow
            # For now, the run stays in PENDING status until a worker picks it up

            return run

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Error triggering workflow run for {workflow_id}: {e}", exc_info=True)
            raise ValueError(f"Failed to trigger workflow run: {e}")

    async def list_workflow_runs(
        self,
        workflow_id: str,
        status: str | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[WorkflowRun], int]:
        """
        List workflow runs with optional filtering and pagination.

        Args:
            workflow_id: Workflow ID
            status: Filter by run status (pending, running, completed, failed)
            page: Page number
            per_page: Items per page

        Returns:
            Tuple of (runs list, total count)

        Raises:
            ValueError: If workflow not found
        """
        try:
            # Verify workflow exists
            workflow = await self.get_workflow_by_id(workflow_id)

            # Build query filters
            filters: dict[str, Any] = {"workflow_definition_id": workflow.id}

            # Filter by status
            if status:
                filters["status"] = status

            # Get total count
            total = await WorkflowRun.find(filters).count()

            # Get paginated results
            skip = (page - 1) * per_page
            runs = await WorkflowRun.find(filters).sort("-started_at").skip(skip).limit(per_page).to_list()

            logger.info(
                f"Listed {len(runs)} workflow runs for {workflow_id} (total: {total}, page: {page}, status: {status})"
            )
            return runs, total

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Error listing workflow runs for {workflow_id}: {e}", exc_info=True)
            raise ValueError(f"Failed to list workflow runs: {e}")

    async def get_workflow_run(self, workflow_id: str, run_id: str) -> tuple[WorkflowRun, list[NodeRun]]:
        """
        Get workflow run detail with all node runs.

        Args:
            workflow_id: Workflow ID
            run_id: Run ID

        Returns:
            Tuple of (WorkflowRun, list of NodeRuns)

        Raises:
            ValueError: If workflow or run not found
        """
        try:
            # Verify workflow exists
            await self.get_workflow_by_id(workflow_id)

            # Get workflow run
            run = await WorkflowRun.get(PydanticObjectId(run_id))
            if not run:
                raise ValueError(f"Workflow run {run_id} not found")

            # Verify run belongs to workflow
            if str(run.workflow_definition_id) != workflow_id:
                raise ValueError(f"Workflow run {run_id} does not belong to workflow {workflow_id}")

            # Get all node runs for this workflow run
            node_runs = await NodeRun.find(NodeRun.workflow_run_id == run.id).sort("started_at").to_list()

            logger.info(f"Retrieved workflow run {run_id} with {len(node_runs)} node runs")
            return run, node_runs

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Error getting workflow run {run_id}: {e}", exc_info=True)
            raise ValueError(f"Failed to get workflow run: {e}")

    def _convert_api_node_to_model(self, api_node: Any) -> WorkflowNode:
        """
        Convert API node input to model WorkflowNode (recursive).

        Args:
            api_node: WorkflowNodeInput from API

        Returns:
            WorkflowNode model instance
        """
        from registry_pkgs.models.workflow import LoopConfig

        # Generate ID if not provided
        node_id = api_node.id if api_node.id else str(uuid4())

        # Convert loop config if provided
        loop_config = None
        if api_node.loopConfig:
            loop_config = LoopConfig(
                max_iterations=api_node.loopConfig.maxIterations,
                end_condition_cel=api_node.loopConfig.endConditionCel,
            )

        # Recursively convert children
        children = [self._convert_api_node_to_model(child) for child in api_node.children]

        return WorkflowNode(
            id=node_id,
            name=api_node.name,
            node_type=api_node.nodeType,
            executor_key=api_node.executorKey,
            config=api_node.config,
            children=children,
            condition_cel=api_node.conditionCel,
            loop_config=loop_config,
        )
