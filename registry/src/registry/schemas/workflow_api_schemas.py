"""
Pydantic Schemas for Workflow Management API v1

These schemas define the request and response models for the
Workflow Management endpoints based on the API documentation.

All schemas use camelCase for API input/output.
"""

from datetime import datetime
from typing import Any

from pydantic import Field

from .case_conversion import APIBaseModel

# ==================== Nested Models ====================


class LoopConfigInput(APIBaseModel):
    """Input/Output schema for loop configuration"""

    maxIterations: int = Field(ge=1, description="Maximum iterations (min: 1)")
    endConditionCel: str | None = Field(None, description="CEL expression for loop termination")


class WorkflowNodeInput(APIBaseModel):
    """Input schema for workflow node (recursive structure)"""

    id: str | None = Field(None, description="Node ID (auto-generated if not provided)")
    name: str = Field(description="Node name")
    nodeType: str = Field(description="Node type: step, parallel, loop, condition, router")
    executorKey: str | None = Field(None, description="MCP tool name or A2A agent name (required for step nodes)")
    config: dict[str, Any] = Field(default_factory=dict, description="Node configuration")
    children: list["WorkflowNodeInput"] = Field(default_factory=list, description="Child nodes for container nodes")
    conditionCel: str | None = Field(None, description="CEL expression for condition/router nodes")
    loopConfig: LoopConfigInput | None = Field(None, description="Loop configuration for loop nodes")


# Enable recursive model
WorkflowNodeInput.model_rebuild()


class WorkflowNodeOutput(APIBaseModel):
    """Output schema for workflow node (recursive structure)"""

    id: str
    name: str
    nodeType: str
    executorKey: str | None = None
    config: dict[str, Any] = {}
    children: list["WorkflowNodeOutput"] = []
    conditionCel: str | None = None
    loopConfig: LoopConfigInput | None = None


# Enable recursive model
WorkflowNodeOutput.model_rebuild()


class ResolvedDependencyInput(APIBaseModel):
    """Input schema for resolved dependency in workflow run retry"""

    nodeId: str = Field(description="Node ID to resolve")
    resolution: str = Field(description="Resolution strategy: reuse_previous_output or rerun")
    sourceNodeRunId: str | None = Field(None, description="Source node run ID when reusing output")


class PaginationMetadata(APIBaseModel):
    """Pagination metadata for list responses"""

    total: int = Field(description="Total number of items")
    page: int = Field(description="Current page number")
    perPage: int = Field(description="Items per page")
    totalPages: int = Field(description="Total number of pages")


# ==================== Request Schemas ====================


class WorkflowCreateRequest(APIBaseModel):
    """Request schema for creating a new workflow"""

    name: str = Field(description="Workflow name")
    description: str | None = Field(None, description="Workflow description")
    nodes: list[WorkflowNodeInput] = Field(description="At least one root node required")


class WorkflowUpdateRequest(APIBaseModel):
    """Request schema for updating a workflow - all fields optional"""

    name: str | None = Field(None, description="Update workflow name")
    description: str | None = Field(None, description="Update workflow description")
    nodes: list[WorkflowNodeInput] | None = Field(None, description="Update workflow nodes")


class WorkflowRunTriggerRequest(APIBaseModel):
    """Request schema for triggering a workflow run"""

    triggerSource: str | None = Field(None, description="Source that triggered the run")
    initialInput: dict[str, Any] | None = Field(None, description="Initial input data for the workflow")
    parentRunId: str | None = Field(None, description="Parent run ID for retry scenarios")
    resolvedDependencies: list[ResolvedDependencyInput] = Field(
        default_factory=list, description="Dependency resolution for retry"
    )


# ==================== Response Schemas ====================


class WorkflowListItem(APIBaseModel):
    """Response schema for workflow list item"""

    id: str
    name: str
    description: str | None = None
    numNodes: int
    createdAt: datetime
    updatedAt: datetime


class WorkflowDetailResponse(APIBaseModel):
    """Response schema for workflow detail"""

    id: str
    name: str
    description: str | None = None
    nodes: list[WorkflowNodeOutput]
    createdAt: datetime
    updatedAt: datetime


class WorkflowListResponse(APIBaseModel):
    """Response schema for workflow list"""

    workflows: list[WorkflowListItem]
    pagination: PaginationMetadata


class WorkflowRunTriggerResponse(APIBaseModel):
    """Response schema for workflow run trigger (202 Accepted)"""

    runId: str
    workflowDefinitionId: str
    status: str
    triggerSource: str | None = None
    startedAt: datetime
    message: str


class WorkflowRunListItem(APIBaseModel):
    """Response schema for workflow run list item"""

    id: str
    workflowDefinitionId: str
    status: str
    triggerSource: str | None = None
    startedAt: datetime
    finishedAt: datetime | None = None
    parentRunId: str | None = None
    errorSummary: str | None = None


class WorkflowRunListResponse(APIBaseModel):
    """Response schema for workflow run list"""

    runs: list[WorkflowRunListItem]
    pagination: PaginationMetadata


class NodeRunOutput(APIBaseModel):
    """Output schema for node run"""

    id: str
    workflowRunId: str
    nodeId: str
    nodeName: str
    status: str
    attempt: int
    inputSnapshot: dict[str, Any] | None = None
    outputSnapshot: dict[str, Any] | None = None
    error: str | None = None
    startedAt: datetime | None = None
    finishedAt: datetime | None = None


class WorkflowRunDetailResponse(APIBaseModel):
    """Response schema for workflow run detail"""

    id: str
    workflowDefinitionId: str
    status: str
    triggerSource: str | None = None
    startedAt: datetime
    finishedAt: datetime | None = None
    initialInput: dict[str, Any] | None = None
    finalOutput: dict[str, Any] | None = None
    errorSummary: str | None = None
    definitionSnapshot: dict[str, Any] | None = None
    parentRunId: str | None = None
    resolvedDependencies: list[ResolvedDependencyInput] = []
    nodeRuns: list[NodeRunOutput] = []


# ==================== Converter Functions ====================


def convert_to_list_item(workflow: Any) -> WorkflowListItem:
    """Convert WorkflowDefinition to WorkflowListItem"""
    from registry_pkgs.models.workflow import WorkflowDefinition

    if not isinstance(workflow, WorkflowDefinition):
        raise ValueError("Expected WorkflowDefinition instance")

    return WorkflowListItem(
        id=str(workflow.id),
        name=workflow.name,
        description=workflow.description,
        numNodes=len(workflow.nodes),
        createdAt=workflow.created_at,
        updatedAt=workflow.updated_at,
    )


def convert_to_detail(workflow: Any) -> WorkflowDetailResponse:
    """Convert WorkflowDefinition to WorkflowDetailResponse"""
    from registry_pkgs.models.workflow import WorkflowDefinition

    if not isinstance(workflow, WorkflowDefinition):
        raise ValueError("Expected WorkflowDefinition instance")

    return WorkflowDetailResponse(
        id=str(workflow.id),
        name=workflow.name,
        description=workflow.description,
        nodes=[_convert_node_to_output(node) for node in workflow.nodes],
        createdAt=workflow.created_at,
        updatedAt=workflow.updated_at,
    )


def _convert_node_to_output(node: Any) -> WorkflowNodeOutput:
    """Convert WorkflowNode to WorkflowNodeOutput (recursive)"""
    return WorkflowNodeOutput(
        id=node.id,
        name=node.name,
        nodeType=node.node_type.value if hasattr(node.node_type, "value") else node.node_type,
        executorKey=node.executor_key,
        config=node.config,
        children=[_convert_node_to_output(child) for child in node.children],
        conditionCel=node.condition_cel,
        loopConfig=(
            LoopConfigInput(
                maxIterations=node.loop_config.max_iterations, endConditionCel=node.loop_config.end_condition_cel
            )
            if node.loop_config
            else None
        ),
    )


def convert_to_run_list_item(run: Any) -> WorkflowRunListItem:
    """Convert WorkflowRun to WorkflowRunListItem"""
    from registry_pkgs.models.workflow import WorkflowRun

    if not isinstance(run, WorkflowRun):
        raise ValueError("Expected WorkflowRun instance")

    return WorkflowRunListItem(
        id=str(run.id),
        workflowDefinitionId=str(run.workflow_definition_id),
        status=run.status.value if hasattr(run.status, "value") else run.status,
        triggerSource=run.trigger_source,
        startedAt=run.started_at,
        finishedAt=run.finished_at,
        parentRunId=str(run.parent_run_id) if run.parent_run_id else None,
        errorSummary=run.error_summary,
    )


def convert_to_run_detail(run: Any, node_runs: list[Any]) -> WorkflowRunDetailResponse:
    """Convert WorkflowRun and NodeRuns to WorkflowRunDetailResponse"""
    from registry_pkgs.models.workflow import WorkflowRun

    if not isinstance(run, WorkflowRun):
        raise ValueError("Expected WorkflowRun instance")

    return WorkflowRunDetailResponse(
        id=str(run.id),
        workflowDefinitionId=str(run.workflow_definition_id),
        status=run.status.value if hasattr(run.status, "value") else run.status,
        triggerSource=run.trigger_source,
        startedAt=run.started_at,
        finishedAt=run.finished_at,
        initialInput=run.initial_input,
        finalOutput=run.final_output,
        errorSummary=run.error_summary,
        definitionSnapshot=run.definition_snapshot,
        parentRunId=str(run.parent_run_id) if run.parent_run_id else None,
        resolvedDependencies=[
            ResolvedDependencyInput(
                nodeId=dep.node_id,
                resolution=dep.resolution.value if hasattr(dep.resolution, "value") else dep.resolution,
                sourceNodeRunId=str(dep.source_node_run_id) if dep.source_node_run_id else None,
            )
            for dep in run.resolved_dependencies
        ],
        nodeRuns=[_convert_node_run_to_output(node_run) for node_run in node_runs],
    )


def _convert_node_run_to_output(node_run: Any) -> NodeRunOutput:
    """Convert NodeRun to NodeRunOutput"""
    from registry_pkgs.models.workflow import NodeRun

    if not isinstance(node_run, NodeRun):
        raise ValueError("Expected NodeRun instance")

    return NodeRunOutput(
        id=str(node_run.id),
        workflowRunId=str(node_run.workflow_run_id),
        nodeId=node_run.node_id,
        nodeName=node_run.node_name,
        status=node_run.status.value if hasattr(node_run.status, "value") else node_run.status,
        attempt=node_run.attempt,
        inputSnapshot=node_run.input_snapshot,
        outputSnapshot=node_run.output_snapshot,
        error=node_run.error,
        startedAt=node_run.started_at,
        finishedAt=node_run.finished_at,
    )
