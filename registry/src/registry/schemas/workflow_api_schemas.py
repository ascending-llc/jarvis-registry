"""
Pydantic Schemas for Workflow Management API v1

These schemas define the request and response models for the
Workflow Management endpoints based on the API documentation.

All schemas use camelCase for API input/output.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import AliasGenerator, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_snake

from registry_pkgs.models.enums import OnRejectPolicy, OnTimeoutPolicy
from registry_pkgs.workflows.hitl.field_types import field_type_to_authoring

from .acl_schema import ResourcePermissions
from .case_conversion import APIBaseModel

_AGNO_RUNTIME_VALIDATION_CONFIG = ConfigDict(
    alias_generator=AliasGenerator(validation_alias=to_snake),
    populate_by_name=True,
)

# ==================== Nested Models ====================


class ViewportInput(APIBaseModel):
    """Input/Output schema for workflow canvas viewport."""

    x: float = Field(default=0, description="Canvas viewport x offset")
    y: float = Field(default=0, description="Canvas viewport y offset")
    zoom: float = Field(default=1, gt=0, description="Canvas viewport zoom level")


class CanvasInput(APIBaseModel):
    """Input/Output schema for workflow canvas metadata."""

    viewport: ViewportInput = Field(description="Canvas viewport state")


class NodePositionInput(APIBaseModel):
    """Input/Output schema for workflow node canvas position."""

    x: float = Field(default=0, description="Node x position on the canvas")
    y: float = Field(default=0, description="Node y position on the canvas")


class StepConfigInput(APIBaseModel):
    """Input/Output schema for step configuration"""

    maxRetries: int = Field(default=0, ge=0, description="Maximum number of retries (min: 0)")
    onError: str = Field(default="fail", description="Error handling strategy: fail, skip, or retry")
    backoffBaseSeconds: float = Field(default=1.0, gt=0, description="Base wait time for first retry (seconds)")
    backoffMaxSeconds: float = Field(default=60.0, gt=0, description="Maximum wait time for any retry (seconds)")


class LoopConfigInput(APIBaseModel):
    """Input/Output schema for loop configuration"""

    maxIterations: int = Field(ge=1, description="Maximum iterations (min: 1)")
    endConditionCel: str | None = Field(None, description="CEL expression for loop termination")


# ── Human Review schemas ─────────────────────────────


class UserInputFieldSchema(APIBaseModel):
    """A single field in a HITL ``userInputSchema`` (form definition shown to the user)."""

    name: str
    fieldType: Literal["string", "number", "boolean", "array"]
    description: str | None = None
    required: bool = False
    defaultValue: Any | None = None


class PendingUserInputField(APIBaseModel):
    """A user-input field surfaced inside a *pending* HITL requirement.

    Source dicts come from agno ``UserInputField.to_dict()`` (snake_case keys).
    agno echoes ``field_type`` back verbatim, so the runtime value reflects what
    the compiler fed it — agno Python type names (``"str"``/``"float"``/...).  We
    normalise it back to the authoring vocabulary (``"string"``/``"number"``/
    ``"boolean"``/``"array"``) so the frontend sees one consistent field-type
    vocabulary on both the authoring side (:class:`UserInputFieldSchema`) and the
    pending side.
    """

    model_config = _AGNO_RUNTIME_VALIDATION_CONFIG

    name: str
    fieldType: str | None = None
    description: str | None = None
    required: bool = True
    value: Any | None = None
    allowedValues: list[Any] | None = None

    @field_validator("fieldType", mode="after")
    @classmethod
    def _normalise_field_type(cls, value: str | None) -> str | None:
        """Map agno's runtime type name back to the authoring vocabulary."""
        return field_type_to_authoring(value)


class HumanReviewConfig(APIBaseModel):
    """Node-level HITL configuration (per-primitive validity enforced by backend).

    Field × node-type compatibility (mirrors agno):

    - ``requiresConfirmation``: Step / Steps / Loop / Router / Condition
    - ``requiresUserInput``:    Step / Router only
    - ``requiresOutputReview``: Step / Router only
    - ``requiresIterationReview``: Loop only
    - ``onReject = else_branch``: Condition only (sends execution to falseSteps)
    """

    requiresConfirmation: bool = False
    confirmationMessage: str | None = None
    requiresUserInput: bool = False
    userInputMessage: str | None = None
    userInputSchema: list[UserInputFieldSchema] | None = None
    requiresOutputReview: bool = False
    outputReviewMessage: str | None = None
    requiresIterationReview: bool = False
    iterationReviewMessage: str | None = None
    onReject: OnRejectPolicy = OnRejectPolicy.SKIP
    timeoutSeconds: int | None = Field(default=None, gt=0)
    onTimeout: OnTimeoutPolicy = OnTimeoutPolicy.CANCEL


class RouterChoiceInput(APIBaseModel):
    """Input schema for a single named choice in a ROUTER node."""

    name: str = Field(description="Choice name (referenced by selector CEL via step_choices)")
    steps: list["WorkflowNodeInput"] = Field(
        default_factory=list,
        description="Steps to execute for this choice (at least 1 required)",
    )


class RouterChoiceOutput(APIBaseModel):
    """Output schema for a single named choice in a ROUTER node."""

    name: str
    steps: list["WorkflowNodeOutput"] = Field(default_factory=list)


class WorkflowNodeInput(APIBaseModel):
    """Input schema for workflow node (recursive structure)"""

    id: str | None = Field(None, description="Node ID (auto-generated if not provided)")
    name: str = Field(description="Node name")
    nodeType: str = Field(description="Node type: step, parallel, loop, condition, router")
    executorKey: str | None = Field(None, description="MCP tool name or A2A agent name (required for step nodes)")
    a2aPool: list[str] = Field(default_factory=list, description="A2A agent pool (max 5 agents)")
    stepConfig: StepConfigInput | None = Field(None, description="Step-level retry and error handling configuration")
    config: dict[str, Any] = Field(default_factory=dict, description="Node configuration")
    position: NodePositionInput = Field(default_factory=NodePositionInput, description="Node position on the canvas")
    children: list["WorkflowNodeInput"] = Field(
        default_factory=list,
        description="Child nodes for PARALLEL and LOOP nodes",
    )
    trueSteps: list["WorkflowNodeInput"] = Field(
        default_factory=list,
        description="Steps executed when CONDITION evaluator is true (at least 1 required for CONDITION nodes)",
    )
    falseSteps: list["WorkflowNodeInput"] = Field(
        default_factory=list,
        description="Steps executed when CONDITION evaluator is false (optional for CONDITION nodes)",
    )
    choices: list[RouterChoiceInput] = Field(
        default_factory=list,
        description="Named choices for ROUTER nodes (at least 2 required)",
    )
    referencedNodeNames: list[str] = Field(
        default_factory=list,
        description=(
            "Names of upstream nodes whose outputs are injected into this node's prompt at runtime. "
            "Only valid on step nodes — sending this on any other node type returns 400."
        ),
    )
    stepObjective: str | None = Field(
        None,
        description=(
            "Required for step nodes: a plain-language statement of what this step should accomplish. "
            "Rendered into the executor's prompt alongside WorkflowDefinition.description and "
            "referenced nodes' objectives. Forbidden on non-step nodes."
        ),
    )
    conditionCel: str | None = Field(None, description="CEL expression for condition/router nodes")
    loopConfig: LoopConfigInput | None = Field(None, description="Loop configuration for loop nodes")
    humanReview: HumanReviewConfig | None = Field(
        default=None,
        description="HITL configuration",
    )


# Enable recursive models
RouterChoiceInput.model_rebuild()
WorkflowNodeInput.model_rebuild()


class WorkflowNodeOutput(APIBaseModel):
    """Output schema for workflow node (recursive structure)"""

    id: str
    name: str
    nodeType: str
    executorKey: str | None = None
    a2aPool: list[str] = Field(default_factory=list)
    stepConfig: StepConfigInput | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    position: NodePositionInput = Field(default_factory=NodePositionInput)
    children: list["WorkflowNodeOutput"] = Field(default_factory=list)
    trueSteps: list["WorkflowNodeOutput"] = Field(default_factory=list)
    falseSteps: list["WorkflowNodeOutput"] = Field(default_factory=list)
    choices: list[RouterChoiceOutput] = Field(default_factory=list)
    referencedNodeNames: list[str] = Field(default_factory=list)
    stepObjective: str | None = None
    conditionCel: str | None = None
    loopConfig: LoopConfigInput | None = None
    # ``null`` when no HITL configured.
    humanReview: HumanReviewConfig | None = None


# Enable recursive models
RouterChoiceOutput.model_rebuild()
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
    canvas: CanvasInput = Field(description="Workflow canvas metadata")
    nodes: list[WorkflowNodeInput] = Field(description="At least one root node required")


class WorkflowUpdateRequest(APIBaseModel):
    """Request schema for updating a workflow - all fields optional"""

    name: str | None = Field(None, description="Update workflow name")
    description: str | None = Field(None, description="Update workflow description")
    canvas: CanvasInput | None = Field(None, description="Update workflow canvas metadata")
    nodes: list[WorkflowNodeInput] | None = Field(None, description="Update workflow nodes")
    enabled: bool | None = Field(None, description="Update workflow enabled status")


class WorkflowRunTriggerRequest(APIBaseModel):
    """Request schema for triggering a workflow run"""

    triggerSource: str | None = Field(None, description="Source that triggered the run")
    initialInput: dict[str, Any] | None = Field(None, description="Initial input data for the workflow")
    parentRunId: str | None = Field(None, description="Parent run ID for retry scenarios")
    resolvedDependencies: list[ResolvedDependencyInput] = Field(
        default_factory=list, description="Dependency resolution for retry"
    )
    version: int | None = Field(
        None, description="Workflow version to run; defaults to the latest version when omitted"
    )


class WorkflowToggleRequest(APIBaseModel):
    """Request schema for toggling workflow status"""

    enabled: bool = Field(..., description="Enable or disable the workflow")


# ==================== Response Schemas ====================


class WorkflowListItem(APIBaseModel):
    """Response schema for workflow list item"""

    id: str
    name: str
    description: str | None = None
    numNodes: int
    enabled: bool = Field(default=False, description="Whether the workflow is enabled")
    version: int = 1
    createdAt: datetime
    updatedAt: datetime
    aclPermission: ResourcePermissions | None = None


class WorkflowDetailResponse(APIBaseModel):
    """Response schema for workflow detail"""

    id: str
    name: str
    description: str | None = None
    canvas: CanvasInput
    nodes: list[WorkflowNodeOutput]
    enabled: bool = Field(default=False, description="Whether the workflow is enabled")
    version: int = 1
    createdAt: datetime
    updatedAt: datetime
    aclPermission: ResourcePermissions | None = None


class WorkflowListResponse(APIBaseModel):
    """Response schema for workflow list"""

    workflows: list[WorkflowListItem]
    pagination: PaginationMetadata


class WorkflowVersionItem(APIBaseModel):
    """Response schema for a single workflow version entry"""

    version: int
    createdAt: datetime
    checksum: str


class WorkflowVersionListResponse(APIBaseModel):
    """Response schema for workflow version history"""

    versions: list[WorkflowVersionItem]


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
    nodeRuns: list["NodeRunOutput"] = Field(default_factory=list)


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


WorkflowRunListItem.model_rebuild()


class StepRequirementSummary(APIBaseModel):
    """A single pending HITL requirement awaiting user decision.

    Returned inside :class:`WorkflowRunDetailResponse.pendingRequirements` when the
    run is ``awaiting_approval``.  Maps 1:1 to agno ``StepRequirement`` fields so the
    frontend can render the correct decision UI (confirm / user_input form /
    output_review with edit / route_selection).

    See ``docs/design/workflow-api-design.md`` § StepRequirementSummary.
    """

    # Source dicts come from agno ``StepRequirement.to_dict()`` (snake_case keys).
    model_config = _AGNO_RUNTIME_VALIDATION_CONFIG

    schemaVersion: int = 1
    stepId: str
    stepName: str | None = None
    stepIndex: int | None = None
    stepType: str | None = None  # step / loop / router / condition / steps

    # Capability flags — drive which UI variant the frontend renders.
    requiresConfirmation: bool = False
    requiresUserInput: bool = False
    requiresOutputReview: bool = False
    requiresRouteSelection: bool = False

    # User-facing prompts
    confirmationMessage: str | None = None
    userInputMessage: str | None = None
    userInputSchema: list[PendingUserInputField] | None = None
    outputReviewMessage: str | None = None
    availableChoices: list[str] | None = None
    allowMultipleSelections: bool = False

    # Post-execution review payload (output_review only)
    stepOutputPreview: dict[str, Any] | None = Field(default=None, validation_alias="step_output")
    isPostExecution: bool = False

    # Decision results (None until the user resolves)
    confirmed: bool | None = None
    rejectionFeedback: str | None = None
    editedOutput: Any | None = None
    userInput: dict[str, Any] | None = None
    selectedChoices: list[str] | None = None

    # Retry + timeout
    retryCount: int = 0
    maxRetries: int | None = None
    timeoutAt: datetime | None = None
    requirementKind: str | None = None
    onTimeout: OnTimeoutPolicy = OnTimeoutPolicy.CANCEL
    onReject: OnRejectPolicy = OnRejectPolicy.SKIP


class WorkflowRunDetailResponse(APIBaseModel):
    """Response schema for workflow run detail"""

    id: str
    workflowDefinitionId: str
    # Version locked at trigger time (snapshot via definitionSnapshot).
    workflowVersion: int | None = None
    status: str
    triggerSource: str | None = None
    startedAt: datetime
    finishedAt: datetime | None = None
    initialInput: dict[str, Any] | None = None
    finalOutput: dict[str, Any] | None = None
    errorSummary: str | None = None
    definitionSnapshot: dict[str, Any] | None = None
    parentRunId: str | None = None
    resolvedDependencies: list[ResolvedDependencyInput] = Field(default_factory=list)
    nodeRuns: list[NodeRunOutput] = Field(default_factory=list)
    # Non-empty iff status == awaiting_approval.  Each element is one
    # pending requirement; the frontend renders a decision UI per element.
    pendingRequirements: list[StepRequirementSummary] = Field(default_factory=list)


# ==================== Converter Functions ====================


def convert_to_list_item(workflow: Any, acl_permission: ResourcePermissions | None = None) -> WorkflowListItem:
    """Convert WorkflowDefinition to WorkflowListItem"""
    from registry_pkgs.models.workflow import WorkflowDefinition

    if not isinstance(workflow, WorkflowDefinition):
        raise ValueError("Expected WorkflowDefinition instance")

    return WorkflowListItem(
        id=str(workflow.id),
        name=workflow.name,
        description=workflow.description,
        numNodes=len(workflow.nodes),
        enabled=workflow.enabled if hasattr(workflow, "enabled") else False,
        version=getattr(workflow, "version", 1),
        createdAt=workflow.created_at,
        updatedAt=workflow.updated_at,
        aclPermission=acl_permission,
    )


def convert_to_detail(workflow: Any, acl_permission: ResourcePermissions | None = None) -> WorkflowDetailResponse:
    """Convert WorkflowDefinition to WorkflowDetailResponse"""
    from registry_pkgs.models.workflow import WorkflowDefinition

    if not isinstance(workflow, WorkflowDefinition):
        raise ValueError("Expected WorkflowDefinition instance")

    return WorkflowDetailResponse(
        id=str(workflow.id),
        name=workflow.name,
        description=workflow.description,
        canvas=_convert_canvas_to_output(workflow.canvas),
        nodes=[_convert_node_to_output(node) for node in workflow.nodes],
        enabled=workflow.enabled if hasattr(workflow, "enabled") else False,
        version=getattr(workflow, "version", 1),
        createdAt=workflow.created_at,
        updatedAt=workflow.updated_at,
        aclPermission=acl_permission,
    )


def _convert_canvas_to_output(canvas: Any) -> CanvasInput:
    """Convert WorkflowCanvas to CanvasInput."""
    return CanvasInput(
        viewport=ViewportInput(
            x=canvas.viewport.x,
            y=canvas.viewport.y,
            zoom=canvas.viewport.zoom,
        ),
    )


def _convert_node_to_output(node: Any) -> WorkflowNodeOutput:
    """Convert WorkflowNode to WorkflowNodeOutput (recursive)"""
    return WorkflowNodeOutput(
        id=node.id,
        name=node.name,
        nodeType=node.node_type.value if hasattr(node.node_type, "value") else node.node_type,
        executorKey=node.executor_key,
        a2aPool=node.a2a_pool,
        stepConfig=(
            StepConfigInput(
                maxRetries=node.step_config.max_retries,
                onError=node.step_config.on_error,
                backoffBaseSeconds=node.step_config.backoff_base_seconds,
                backoffMaxSeconds=node.step_config.backoff_max_seconds,
            )
            if node.step_config
            else None
        ),
        config=node.config,
        position=NodePositionInput(x=node.position.x, y=node.position.y),
        children=[_convert_node_to_output(child) for child in node.children],
        trueSteps=[_convert_node_to_output(child) for child in node.true_steps],
        falseSteps=[_convert_node_to_output(child) for child in node.false_steps],
        choices=[
            RouterChoiceOutput(
                name=choice.name,
                steps=[_convert_node_to_output(s) for s in choice.steps],
            )
            for choice in node.choices
        ],
        referencedNodeNames=node.referenced_node_names,
        stepObjective=node.step_objective,
        conditionCel=node.condition_cel,
        loopConfig=(
            LoopConfigInput(
                maxIterations=node.loop_config.max_iterations, endConditionCel=node.loop_config.end_condition_cel
            )
            if node.loop_config
            else None
        ),
        # Serialize the embedded HumanReviewSpec (None when no HITL configured).
        humanReview=(
            HumanReviewConfig(
                requiresConfirmation=node.human_review.requires_confirmation,
                confirmationMessage=node.human_review.confirmation_message,
                requiresUserInput=node.human_review.requires_user_input,
                userInputMessage=node.human_review.user_input_message,
                userInputSchema=(
                    [
                        UserInputFieldSchema(
                            name=f.name,
                            fieldType=f.field_type,
                            description=f.description,
                            required=f.required,
                            defaultValue=f.default_value,
                        )
                        for f in node.human_review.user_input_schema
                    ]
                    if node.human_review.user_input_schema
                    else None
                ),
                requiresOutputReview=node.human_review.requires_output_review,
                outputReviewMessage=node.human_review.output_review_message,
                requiresIterationReview=node.human_review.requires_iteration_review,
                iterationReviewMessage=node.human_review.iteration_review_message,
                onReject=node.human_review.on_reject,
                timeoutSeconds=node.human_review.timeout_seconds,
                onTimeout=node.human_review.on_timeout,
            )
            if node.human_review
            else None
        ),
    )


def convert_node_to_input(node: Any) -> WorkflowNodeInput:
    """Convert WorkflowNode to WorkflowNodeInput (recursive)."""
    return WorkflowNodeInput(
        id=node.id,
        name=node.name,
        nodeType=node.node_type.value if hasattr(node.node_type, "value") else node.node_type,
        executorKey=node.executor_key,
        a2aPool=node.a2a_pool,
        stepConfig=(
            StepConfigInput(
                maxRetries=node.step_config.max_retries,
                onError=node.step_config.on_error,
                backoffBaseSeconds=node.step_config.backoff_base_seconds,
                backoffMaxSeconds=node.step_config.backoff_max_seconds,
            )
            if node.step_config
            else None
        ),
        config=node.config,
        children=[convert_node_to_input(child) for child in node.children],
        trueSteps=[convert_node_to_input(child) for child in node.true_steps],
        falseSteps=[convert_node_to_input(child) for child in node.false_steps],
        choices=[
            RouterChoiceInput(
                name=choice.name,
                steps=[convert_node_to_input(s) for s in choice.steps],
            )
            for choice in node.choices
        ],
        referencedNodeNames=node.referenced_node_names,
        stepObjective=node.step_objective,
        conditionCel=node.condition_cel,
        loopConfig=(
            LoopConfigInput(
                maxIterations=node.loop_config.max_iterations, endConditionCel=node.loop_config.end_condition_cel
            )
            if node.loop_config
            else None
        ),
        humanReview=(
            HumanReviewConfig(
                requiresConfirmation=node.human_review.requires_confirmation,
                confirmationMessage=node.human_review.confirmation_message,
                requiresUserInput=node.human_review.requires_user_input,
                userInputMessage=node.human_review.user_input_message,
                userInputSchema=(
                    [
                        UserInputFieldSchema(
                            name=f.name,
                            fieldType=f.field_type,
                            description=f.description,
                            required=f.required,
                            defaultValue=f.default_value,
                        )
                        for f in node.human_review.user_input_schema
                    ]
                    if node.human_review.user_input_schema
                    else None
                ),
                requiresOutputReview=node.human_review.requires_output_review,
                outputReviewMessage=node.human_review.output_review_message,
                requiresIterationReview=node.human_review.requires_iteration_review,
                iterationReviewMessage=node.human_review.iteration_review_message,
                onReject=node.human_review.on_reject,
                timeoutSeconds=node.human_review.timeout_seconds,
                onTimeout=node.human_review.on_timeout,
            )
            if node.human_review
            else None
        ),
    )


def convert_to_run_list_item(run: Any, node_runs: list[Any] | None = None) -> WorkflowRunListItem:
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
        nodeRuns=[_convert_node_run_to_output(node_run) for node_run in node_runs or []],
    )


def convert_to_run_detail(run: Any, node_runs: list[Any]) -> WorkflowRunDetailResponse:
    """Convert WorkflowRun and NodeRuns to WorkflowRunDetailResponse"""
    from registry_pkgs.models.workflow import WorkflowRun

    if not isinstance(run, WorkflowRun):
        raise ValueError("Expected WorkflowRun instance")

    return WorkflowRunDetailResponse(
        id=str(run.id),
        workflowDefinitionId=str(run.workflow_definition_id),
        workflowVersion=run.workflow_version,
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
        # Surface the serialized agno StepRequirements to the frontend.
        # Pydantic validates each dict against StepRequirementSummary — extra fields
        # from agno are tolerated (model_config defaults to extra='ignore').
        pendingRequirements=[StepRequirementSummary.model_validate(req) for req in (run.pending_requirements or [])],
    )


def convert_node_run_to_output(node_run: Any) -> NodeRunOutput:
    """Convert NodeRun to NodeRunOutput"""
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


def _convert_node_run_to_output(node_run: Any) -> NodeRunOutput:
    """Backward-compatible private alias for existing converter call sites."""
    return convert_node_run_to_output(node_run)
