"""
Workflow Service - Business logic for Workflow Management API

This service handles all workflow-related operations using MongoDB and Beanie ODM.
"""

import hashlib
import logging
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from beanie import PydanticObjectId
from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from registry_pkgs.database.decorators import get_current_session, use_transaction
from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.models.a2a_agent import A2AAgent
from registry_pkgs.models.enums import WorkflowNodeType, WorkflowRunStatus
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer
from registry_pkgs.models.workflow import (
    HumanReviewSpec,
    LoopConfig,
    NodeRun,
    RouterChoice,
    StepConfig,
    UserInputField,
    WorkflowCanvas,
    WorkflowDefinition,
    WorkflowNode,
    WorkflowNodePosition,
    WorkflowRun,
    WorkflowVersion,
)

from ..schemas.workflow_api_schemas import WorkflowCreateRequest, WorkflowUpdateRequest

logger = logging.getLogger(__name__)

_BUILTIN_EXECUTOR_KEYS = {"echo", "set_value"}


def _convert_human_review(api_human_review: Any) -> HumanReviewSpec | None:
    """Translate the ``humanReview`` API input into the embedded ``HumanReviewSpec``.

    Returns ``None`` when no HITL is configured on this node.  Per-node-type
    field-compatibility validation runs inside ``WorkflowNode._validate_shape``.
    """
    if not api_human_review:
        return None
    hr = api_human_review
    return HumanReviewSpec(
        requires_confirmation=hr.requiresConfirmation,
        confirmation_message=hr.confirmationMessage,
        requires_user_input=hr.requiresUserInput,
        user_input_message=hr.userInputMessage,
        user_input_schema=(
            [
                UserInputField(
                    name=f.name,
                    field_type=f.fieldType,
                    description=f.description,
                    required=f.required,
                    default_value=f.defaultValue,
                )
                for f in hr.userInputSchema
            ]
            if hr.userInputSchema
            else None
        ),
        requires_output_review=hr.requiresOutputReview,
        output_review_message=hr.outputReviewMessage,
        requires_iteration_review=hr.requiresIterationReview,
        iteration_review_message=hr.iterationReviewMessage,
        on_reject=hr.onReject,
        timeout_seconds=hr.timeoutSeconds,
        on_timeout=hr.onTimeout,
    )


class WorkflowService:
    """Service for Workflow operations"""

    @staticmethod
    def _checksum(definition_json: str) -> str:
        """Return the sha256 hex digest of a definition's JSON serialization."""
        return hashlib.sha256(definition_json.encode("utf-8")).hexdigest()

    @staticmethod
    def _iter_nodes(nodes: list[WorkflowNode]) -> Iterator[WorkflowNode]:
        """Yield every node in a workflow tree, including nested branches."""
        for node in nodes:
            yield node
            yield from WorkflowService._iter_nodes(node.children)
            yield from WorkflowService._iter_nodes(node.true_steps)
            yield from WorkflowService._iter_nodes(node.false_steps)
            for choice in node.choices:
                yield from WorkflowService._iter_nodes(choice.steps)

    async def _validate_executor_refs(self, nodes: list[WorkflowNode]) -> None:
        """Ensure workflow executor references resolve before saving the definition."""
        executor_keys: set[str] = set()
        pool_paths: set[str] = set()

        for node in self._iter_nodes(nodes):
            if node.node_type != WorkflowNodeType.STEP:
                continue
            if node.executor_key:
                executor_keys.add(node.executor_key.lstrip("/"))
            if node.a2a_pool:
                pool_paths.update(path.lstrip("/") for path in node.a2a_pool)

        executor_keys_to_check = executor_keys - _BUILTIN_EXECUTOR_KEYS
        matched_mcp_keys: set[str] = set()
        if executor_keys_to_check:
            mcp_servers = await ExtendedMCPServer.find(
                {"serverName": {"$in": sorted(executor_keys_to_check)}, "config.enabled": True}
            ).to_list()
            matched_mcp_keys = {server.serverName for server in mcp_servers}

        unmatched_executor_keys = executor_keys_to_check - matched_mcp_keys
        matched_a2a_executor_keys: set[str] = set()
        if unmatched_executor_keys:
            a2a_agents = await A2AAgent.find(
                {"path": {"$in": sorted(unmatched_executor_keys)}, "config.enabled": True}
            ).to_list()
            matched_a2a_executor_keys = {agent.path for agent in a2a_agents}

        unknown_executor_keys = unmatched_executor_keys - matched_a2a_executor_keys
        if unknown_executor_keys:
            key = sorted(unknown_executor_keys)[0]
            msg = f"Unknown executor key: {key!r}"
            logger.warning(msg)
            raise HTTPException(status_code=400, detail=msg)

        if not pool_paths:
            return

        pool_agents = await A2AAgent.find({"path": {"$in": sorted(pool_paths)}, "config.enabled": True}).to_list()
        matched_pool_paths = {agent.path for agent in pool_agents}
        unknown_pool_paths = pool_paths - matched_pool_paths
        if unknown_pool_paths:
            path = sorted(unknown_pool_paths)[0]
            msg = f"Unknown a2aPool agent path: {path!r}"
            logger.warning(msg)
            raise HTTPException(status_code=400, detail=msg)

    async def list_workflows(
        self,
        query: str | None = None,
        page: int = 1,
        per_page: int = 20,
        accessible_workflow_ids: list[str] | None = None,
    ) -> tuple[list[WorkflowDefinition], int]:
        """
        List workflows with optional filtering and pagination.

        Args:
            query: Free-text search across workflow name, description
            page: Page number (validated by router)
            per_page: Items per page (validated by router)
            accessible_workflow_ids: When provided, restrict results to these workflow IDs
                (ACL VIEW filter). An empty list yields no results.

        Returns:
            Tuple of (workflows list, total count)
        """
        try:
            # Build query filters
            filters: dict[str, Any] = {}

            # Restrict to ACL-accessible workflows
            if accessible_workflow_ids is not None:
                filters["_id"] = {"$in": [PydanticObjectId(wid) for wid in accessible_workflow_ids]}

            # Free-text search
            if query:
                search_pattern = {"$regex": re.escape(query), "$options": "i"}
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

        except Exception:
            logger.exception("Error listing workflows")
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
            try:
                workflow_oid = PydanticObjectId(workflow_id)
            except Exception as exc:
                raise ValueError(f"Invalid workflow ID: {workflow_id}") from exc

            workflow = await WorkflowDefinition.get(workflow_oid)
            if not workflow:
                raise ValueError(f"Workflow {workflow_id} not found")

            logger.info(f"Retrieved workflow {workflow_id}: {workflow.name}")
            return workflow

        except ValueError:
            raise
        except Exception:
            logger.exception("Error getting workflow %s", workflow_id)
            raise

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
            await self._validate_executor_refs(nodes)

            # Create workflow definition
            # Always set enabled to False during creation (regardless of frontend input)
            workflow = WorkflowDefinition(
                name=data.name,
                description=data.description,
                canvas=self._convert_api_canvas_to_model(data.canvas),
                nodes=nodes,
                enabled=False,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )

            # Save to database (this will trigger Pydantic validation)
            await workflow.insert()

            logger.info(f"Created workflow {workflow.id}: {workflow.name} (enabled: False)")
            return workflow

        except ValueError as e:
            logger.error(f"Validation error creating workflow: {e}")
            raise
        except HTTPException:
            raise
        except Exception:
            logger.exception("Error creating workflow")
            raise

    @use_transaction
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
            ValueError: If workflow not found or validation fails.
            HTTPException(409): If the workflow was modified concurrently.
        """
        try:
            # Get existing workflow
            workflow = await self.get_workflow_by_id(workflow_id)

            # Capture the current version's definition for history BEFORE mutating it.
            previous_version = workflow.version
            previous_definition = workflow.model_dump(mode="json")
            previous_checksum = self._checksum(workflow.model_dump_json())
            previous_updated_at = workflow.updated_at

            # Build and validate the update FIRST. Node conversion runs the model-level
            # shape validators, so an invalid update raises here — before anything is
            # written — and can never leave an orphan/duplicate version-history row.
            update_fields: dict[str, Any] = {
                "version": previous_version + 1,
                "updated_at": datetime.now(UTC),
            }

            if data.name is not None:
                update_fields["name"] = data.name

            if data.description is not None:
                update_fields["description"] = data.description

            if data.canvas is not None:
                update_fields["canvas"] = self._convert_api_canvas_to_model(data.canvas).model_dump(mode="json")

            if data.nodes is not None:
                nodes = [self._convert_api_node_to_model(node) for node in data.nodes]
                await self._validate_executor_refs(nodes)
                update_fields["nodes"] = [node.model_dump(mode="json") for node in nodes]

            if data.enabled is not None:
                update_fields["enabled"] = data.enabled

            # Always update the timestamp
            update_fields["updated_at"] = datetime.now(UTC)

            session = get_current_session()
            collection = MongoDB.get_database().get_collection(WorkflowDefinition.get_settings().name)

            updated_doc = await collection.find_one_and_update(
                {"_id": workflow.id, "version": previous_version},
                {"$set": update_fields},
                return_document=ReturnDocument.AFTER,
                session=session,
            )
            if updated_doc is None:
                raise HTTPException(
                    status_code=409,
                    detail="Workflow was modified concurrently; re-fetch and retry",
                )

            # Archive the prior definition atomically with the bump.  The unique
            # (workflow_id, version) index is the backstop: a racing archive of the
            # same version raises DuplicateKeyError → 409.
            try:
                await WorkflowVersion(
                    workflow_id=workflow.id,
                    version=previous_version,
                    definition=previous_definition,
                    checksum=previous_checksum,
                    created_at=previous_updated_at,
                ).insert(session=session)
            except DuplicateKeyError as exc:
                raise HTTPException(
                    status_code=409,
                    detail="Workflow version already archived; concurrent update detected",
                ) from exc

            logger.info(f"Updated workflow {workflow_id}: version {previous_version} → {update_fields['version']}")
            return await WorkflowDefinition.get(workflow.id, session=session)

        except (ValueError, HTTPException):
            raise
        except Exception:
            logger.exception("Error updating workflow %s", workflow_id)
            raise

    async def list_versions(self, workflow_id: str) -> list[dict[str, Any]]:
        """
        List all versions of a workflow (history + current), oldest first.

        Returns:
            List of {version, created_at, checksum} dicts.

        Raises:
            ValueError: If workflow not found.
        """
        try:
            workflow = await self.get_workflow_by_id(workflow_id)

            history = await WorkflowVersion.find({"workflow_id": workflow.id}).sort("version").to_list()
            versions: list[dict[str, Any]] = [
                {"version": v.version, "created_at": v.created_at, "checksum": v.checksum} for v in history
            ]

            # Append the current (latest) version, which is not in the history collection.
            versions.append(
                {
                    "version": workflow.version,
                    "created_at": workflow.updated_at,
                    "checksum": self._checksum(workflow.model_dump_json()),
                }
            )
            return versions

        except ValueError:
            raise
        except Exception:
            logger.exception("Error listing versions for workflow %s", workflow_id)
            raise

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

            # Delete all node runs for these workflow runs and the workflow runs themselves
            if run_ids:
                await NodeRun.find({"workflow_run_id": {"$in": run_ids}}).delete()
                logger.info(f"Deleted node runs for {len(run_ids)} workflow runs")

                # agno persists per-run state (HumanReview pauses, step outputs) keyed
                # by ``session_id == str(run.id)`` — clean it up so we don't leave
                # orphan documents (and user input data) behind.
                session_ids = [str(rid) for rid in run_ids]
                db = MongoDB.get_database()
                result = await db.get_collection("agno_workflow_sessions").delete_many(
                    {"session_id": {"$in": session_ids}}
                )
                logger.info(f"Deleted {result.deleted_count} agno_workflow_sessions for workflow {workflow_id}")

                await WorkflowRun.find({"_id": {"$in": run_ids}}).delete()
                logger.info(f"Deleted {len(run_ids)} workflow runs and their node runs for workflow {workflow_id}")

            # Delete version history
            await WorkflowVersion.find({"workflow_id": workflow.id}).delete()

            # Delete the workflow
            await workflow.delete()

            logger.info(f"Deleted workflow {workflow_id}: {workflow.name}")
            return True

        except ValueError:
            raise
        except Exception:
            logger.exception("Error deleting workflow %s", workflow_id)
            raise

    async def toggle_workflow_status(
        self,
        workflow_id: str,
        enabled: bool,
    ) -> WorkflowDefinition:
        """
        Toggle workflow enabled/disabled status.

        Args:
            workflow_id: Workflow ID
            enabled: Enable (True) or disable (False)

        Returns:
            Updated WorkflowDefinition document

        Raises:
            ValueError: If workflow not found
        """
        try:
            # Get existing workflow
            workflow = await self.get_workflow_by_id(workflow_id)

            # Update enabled field
            workflow.enabled = enabled
            workflow.updated_at = datetime.now(UTC)

            # Save to database
            await workflow.save()

            logger.info(f"Toggled workflow {workflow.name} (ID: {workflow.id}) enabled to {enabled}")
            return workflow

        except ValueError:
            raise
        except Exception:
            logger.exception("Error toggling workflow %s", workflow_id)
            raise

    async def trigger_workflow_run(
        self,
        workflow_id: str,
        trigger_source: str | None = None,
        initial_input: dict[str, Any] | None = None,
        parent_run_id: str | None = None,
        resolved_dependencies: list[dict[str, Any]] | None = None,
        version: int | None = None,
        triggering_user_id: str | None = None,
        triggering_username: str | None = None,
        triggering_scopes: list[str] | None = None,
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
            version: Workflow version to run; defaults to the latest version when omitted

        Returns:
            Created WorkflowRun document (status=PENDING)

        Raises:
            ValueError: If workflow not found or validation fails
        """
        try:
            # Get existing workflow
            workflow = await self.get_workflow_by_id(workflow_id)

            # Check if workflow is enabled
            if not workflow.enabled:
                raise ValueError(
                    f"Workflow '{workflow.name}' is disabled. Please enable the workflow before triggering a run."
                )

            # Resolve the definition snapshot for the requested version (defaults to latest).
            if version is not None and version != workflow.version:
                workflow_version = await WorkflowVersion.find_one({"workflow_id": workflow.id, "version": version})
                if not workflow_version:
                    raise ValueError(f"Workflow {workflow_id} version {version} not found")
                definition_snapshot = workflow_version.definition
                resolved_version = version
            else:
                definition_snapshot = workflow.model_dump(mode="json")
                resolved_version = workflow.version

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
                workflow_version=resolved_version,
                status=WorkflowRunStatus.PENDING,
                trigger_source=trigger_source,
                started_at=datetime.now(UTC),
                initial_input=initial_input,
                definition_snapshot=definition_snapshot,
                parent_run_id=PydanticObjectId(parent_run_id) if parent_run_id else None,
                resolved_dependencies=resolved_deps,
                triggering_user_id=triggering_user_id,
                triggering_username=triggering_username,
                triggering_scopes=triggering_scopes,
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
        except Exception:
            logger.exception("Error triggering workflow run for %s", workflow_id)
            raise

    async def list_workflow_runs(
        self,
        workflow_id: str,
        status: str | None = None,
        page: int = 1,
        per_page: int = 20,
    ) -> tuple[list[tuple[WorkflowRun, list[NodeRun]]], int]:
        """
        List workflow runs with optional filtering and pagination.

        Args:
            workflow_id: Workflow ID
            status: Filter by run status (pending, running, paused, completed, failed, cancelled)
            page: Page number
            per_page: Items per page

        Returns:
            Tuple of ((run, node runs) list, total count)

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
            run_ids = [run.id for run in runs]
            node_runs_by_run_id: dict[str, list[NodeRun]] = {str(run_id): [] for run_id in run_ids}

            if run_ids:
                node_runs = await NodeRun.find({"workflow_run_id": {"$in": run_ids}}).sort("started_at").to_list()
                for node_run in node_runs:
                    node_runs_by_run_id.setdefault(str(node_run.workflow_run_id), []).append(node_run)

            runs_with_nodes = [(run, node_runs_by_run_id.get(str(run.id), [])) for run in runs]

            logger.info(
                f"Listed {len(runs)} workflow runs for {workflow_id} (total: {total}, page: {page}, status: {status})"
            )
            return runs_with_nodes, total

        except ValueError:
            raise
        except Exception:
            logger.exception("Error listing workflow runs for %s", workflow_id)
            raise

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
        except Exception:
            logger.exception("Error getting workflow run %s", run_id)
            raise

    def _convert_api_canvas_to_model(self, api_canvas: Any) -> WorkflowCanvas:
        """Convert API canvas input to model WorkflowCanvas."""
        return WorkflowCanvas(
            viewport={
                "x": api_canvas.viewport.x,
                "y": api_canvas.viewport.y,
                "zoom": api_canvas.viewport.zoom,
            },
        )

    def _convert_api_node_to_model(self, api_node: Any) -> WorkflowNode:
        """
        Convert API node input to model WorkflowNode (recursive).

        Args:
            api_node: WorkflowNodeInput from API

        Returns:
            WorkflowNode model instance
        """
        # Generate ID if not provided
        node_id = api_node.id if api_node.id else str(uuid4())

        # Convert loop config if provided
        loop_config = None
        if api_node.loopConfig:
            loop_config = LoopConfig(
                max_iterations=api_node.loopConfig.maxIterations,
                end_condition_cel=api_node.loopConfig.endConditionCel,
            )

        # Convert step config if provided
        step_config = None
        if api_node.stepConfig:
            step_config = StepConfig(
                max_retries=api_node.stepConfig.maxRetries,
                on_error=api_node.stepConfig.onError,
                backoff_base_seconds=api_node.stepConfig.backoffBaseSeconds,
                backoff_max_seconds=api_node.stepConfig.backoffMaxSeconds,
            )

        children = [self._convert_api_node_to_model(child) for child in api_node.children]
        true_steps = [self._convert_api_node_to_model(child) for child in api_node.trueSteps]
        false_steps = [self._convert_api_node_to_model(child) for child in api_node.falseSteps]
        choices = [
            RouterChoice(
                name=choice.name,
                steps=[self._convert_api_node_to_model(s) for s in choice.steps],
            )
            for choice in api_node.choices
        ]

        human_review = _convert_human_review(api_node.humanReview)

        return WorkflowNode(
            id=node_id,
            name=api_node.name,
            node_type=api_node.nodeType,
            executor_key=api_node.executorKey,
            a2a_pool=api_node.a2aPool,
            step_config=step_config,
            config=api_node.config,
            position=WorkflowNodePosition(x=api_node.position.x, y=api_node.position.y),
            children=children,
            true_steps=true_steps,
            false_steps=false_steps,
            choices=choices,
            condition_cel=api_node.conditionCel,
            loop_config=loop_config,
            human_review=human_review,
        )
