from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from beanie import Document, PydanticObjectId
from pydantic import BaseModel, Field, field_validator, model_validator
from pymongo import ASCENDING, IndexModel

from registry_pkgs.models.enums import (
    NodeRunStatus,
    OnRejectPolicy,
    OnTimeoutPolicy,
    ResolvedDependencyResolution,
    WorkflowDirective,
    WorkflowNodeType,
    WorkflowRunStatus,
)

logger = logging.getLogger(__name__)


class StepConfig(BaseModel):
    """Per-step execution controls for STEP nodes.

    Attributes:
        max_retries:          Maximum number of additional attempts after the first failure.
                              When ``on_error="retry"`` the retry loop is managed by the
                              executor wrapper with exponential backoff, so agno receives
                              ``max_retries=0`` and does not interfere.  When ``on_error`` is
                              ``"fail"`` or ``"skip"`` this value is forwarded to agno for
                              its own step-level retry logic.
        on_error:             Behaviour when the step ultimately fails after all attempts.
                              ``"fail"``  – abort the whole workflow run (default).
                              ``"skip"``  – record the failure and continue to the next step.
                              ``"retry"`` – retry up to ``max_retries`` times with exponential
                                            backoff before falling through to ``"fail"``
                                            behaviour.  The retry loop is managed by the
                                            executor wrapper, not by agno, so directives
                                            (pause/cancel) are honoured between attempts.
        backoff_base_seconds: Base wait time in seconds for the first retry backoff interval.
                              Each subsequent interval doubles: base * 2^attempt.
        backoff_max_seconds:  Upper cap for any single backoff interval (seconds).
    """

    max_retries: int = 0
    on_error: str = "fail"
    backoff_base_seconds: float = 1.0
    backoff_max_seconds: float = 60.0

    @field_validator("max_retries")
    @classmethod
    def _validate_max_retries(cls, value: int) -> int:
        if value < 0:
            raise ValueError("max_retries must be >= 0")
        return value

    @field_validator("on_error")
    @classmethod
    def _validate_on_error(cls, value: str) -> str:
        allowed = {"fail", "skip", "retry"}
        if value not in allowed:
            raise ValueError(f"on_error must be one of {sorted(allowed)}, got {value!r}")
        return value

    @field_validator("backoff_base_seconds", "backoff_max_seconds")
    @classmethod
    def _validate_backoff(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("backoff seconds must be > 0")
        return value


class LoopConfig(BaseModel):
    """Configuration for a loop node."""

    max_iterations: int = 10
    # CEL expression returning bool; True exits the loop.
    end_condition_cel: str | None = None

    @field_validator("max_iterations")
    @classmethod
    def _validate_max_iterations(cls, value: int) -> int:
        if value < 1:
            raise ValueError("loop max_iterations must be >= 1")
        return value


class WorkflowViewport(BaseModel):
    """Viewport state for the workflow canvas."""

    x: float = 0
    y: float = 0
    zoom: float = 1


class WorkflowCanvas(BaseModel):
    """Canvas metadata for rendering a workflow in the frontend."""

    viewport: WorkflowViewport = Field(default_factory=WorkflowViewport)


class WorkflowNodePosition(BaseModel):
    """Node position on the workflow canvas."""

    x: float = 0
    y: float = 0


class UserInputField(BaseModel):
    """Schema element used by ``HumanReviewSpec.user_input_schema``"""

    name: str
    field_type: Literal["string", "number", "boolean", "array"]
    description: str | None = None
    required: bool = False
    default_value: Any | None = None


class HumanReviewSpec(BaseModel):
    """Per-node HITL configuration translated to agno ``HumanReview`` at compile time.

    Field compatibility (validated in :meth:`WorkflowNode._validate_shape`):

    ============================  =============  =============  =============  =============  =============
    Field                         Step           Steps          Condition      Loop           Router
    ============================  =============  =============  =============  =============  =============
    requires_confirmation          ✓             ✓              ✓              ✓               ✓
    requires_user_input            ✓             —              —              —               ✓
    requires_output_review         ✓             —              —              —               ✓
    requires_iteration_review      —             —              —              ✓               —
    on_reject (else_branch)        —             —              ✓ only         —               —
    ============================  =============  =============  =============  =============  =============

    Parallel nodes do not support any HITL field (agno itself rejects them).
    """

    requires_confirmation: bool = False
    confirmation_message: str | None = None
    # Step / Router only — collect parameters at runtime via dynamic form
    requires_user_input: bool = False
    user_input_message: str | None = None
    user_input_schema: list[UserInputField] | None = None
    # Step / Router only — post-execution review (user can accept/edit/reject the step output)
    requires_output_review: bool = False
    output_review_message: str | None = None
    # Loop only — pause after each iteration
    requires_iteration_review: bool = False
    iteration_review_message: str | None = None
    # Shared behaviour
    on_reject: OnRejectPolicy = OnRejectPolicy.SKIP
    # timeout_seconds → agno stamps a per-requirement ``timeout_at`` deadline.
    # agno applies on_timeout only at continue-time (not via a background timer),
    # so WorkflowControlService.get_run_status nudges continue_run once a polled
    # AWAITING_APPROVAL run is past the deadline.
    timeout_seconds: int | None = None
    on_timeout: OnTimeoutPolicy = OnTimeoutPolicy.CANCEL

    @field_validator("timeout_seconds")
    @classmethod
    def _validate_timeout(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("timeout_seconds must be > 0")
        return value


class RouterChoice(BaseModel):
    """A single named choice in a ROUTER node, containing one or more steps.

    The choice ``name`` is what the router's ``selector`` CEL expression must return
    to dispatch into this branch. ``steps`` is the sequential pipeline executed when
    the choice is selected; choices are compiled into an agno ``Steps`` container
    regardless of whether they contain one step or many.
    """

    name: str
    steps: list[WorkflowNode] = Field(default_factory=list)

    @field_validator("steps")
    @classmethod
    def _validate_steps_not_empty(cls, value: list[WorkflowNode]) -> list[WorkflowNode]:
        if not value:
            raise ValueError("router choice requires at least one step")
        return value


class WorkflowNode(BaseModel):
    """Definition of a single node in a workflow graph."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    # Step nodes only: MCP tool name or a2a agent name
    node_type: WorkflowNodeType = WorkflowNodeType.STEP

    executor_key: str | None = None
    a2a_pool: list[str] = Field(default_factory=list)
    # Per-step retry and error-handling policy (STEP nodes only).
    step_config: StepConfig | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    position: WorkflowNodePosition = Field(default_factory=WorkflowNodePosition)

    # When set, agno's execution loop pauses at this node and emits a
    # StepRequirement which we surface via WorkflowRun.pending_requirements.
    human_review: HumanReviewSpec | None = None

    # Child nodes used by PARALLEL and LOOP container nodes.
    children: list[WorkflowNode] = Field(default_factory=list)

    # CONDITION-only: sequential branches.
    true_steps: list[WorkflowNode] = Field(default_factory=list)
    false_steps: list[WorkflowNode] = Field(default_factory=list)

    # ROUTER-only: named choices.
    choices: list[RouterChoice] = Field(default_factory=list)

    # Names of previously-executed nodes whose outputs should be explicitly injected
    # into this node's prompt at runtime.  The compiler wraps the executor with
    # _with_intention_data which pulls each name from StepInput.previous_step_outputs
    # and includes it in the structured prompt.  Only valid on STEP nodes;
    # non-STEP nodes raise ValueError in _validate_shape.
    referenced_node_names: list[str] = Field(default_factory=list)

    # Plain-language statement of what this step must accomplish.
    # Required on STEP nodes; forbidden on all other node types.
    # Rendered into the executor's prompt alongside WorkflowDefinition.description
    # and referenced nodes' objectives via render_step_prompt().
    step_objective: str | None = None

    # CEL expression used by condition / router nodes.
    # Condition: returns bool; available variables: input, previous_step_content,
    #            previous_step_outputs, additional_data, session_state
    # Router:    returns a choice name string; additional variable:
    #            step_choices (list of all choice names)
    condition_cel: str | None = None
    loop_config: LoopConfig | None = None

    @field_validator("step_objective")
    @classmethod
    def _normalize_step_objective(cls, value: str | None) -> str | None:
        if value is None:
            return value
        collapsed = re.sub(r"\s+", " ", value).strip()
        return collapsed or None

    @field_validator("a2a_pool")
    @classmethod
    def _validate_a2a_pool(cls, value: list[str]) -> list[str]:
        if len(value) > 5:
            raise ValueError("a2a_pool must contain at most 5 agents")
        return value

    @model_validator(mode="after")
    def _validate_shape(self) -> WorkflowNode:
        # HITL: enforce per-node-type field compatibility for ``human_review``.
        # Mirrors agno's validate_human_review_for_step / loop / router / condition / steps
        _validate_human_review_for_node(self.node_type, self.human_review)

        if self.node_type == WorkflowNodeType.STEP:
            has_key = bool(self.executor_key)
            has_pool = bool(self.a2a_pool)
            if not has_key and not has_pool:
                raise ValueError("step node requires either executor_key or a2a_pool")
            if has_key and has_pool:
                raise ValueError("step node must not define both executor_key and a2a_pool")
            if self.children:
                raise ValueError("step node must not have children")
            if self.true_steps or self.false_steps:
                raise ValueError("step node must not define true_steps/false_steps")
            if self.choices:
                raise ValueError("step node must not define choices")
            if self.condition_cel is not None:
                raise ValueError("step node must not define condition_cel")
            if self.loop_config is not None:
                raise ValueError("step node must not define loop_config")
            if not self.step_objective:
                raise ValueError("step node requires step_objective")
            return self

        if self.referenced_node_names:
            raise ValueError("referenced_node_names is only supported on step nodes")
        if self.step_objective is not None:
            raise ValueError("step_objective is only valid on step nodes")

        if self.node_type == WorkflowNodeType.PARALLEL:
            if len(self.children) < 2:
                raise ValueError("parallel node requires at least 2 children")
            if self.true_steps or self.false_steps:
                raise ValueError("parallel node must not define true_steps/false_steps")
            if self.choices:
                raise ValueError("parallel node must not define choices")
            if self.condition_cel is not None:
                raise ValueError("parallel node must not define condition_cel")
            if self.loop_config is not None:
                raise ValueError("parallel node must not define loop_config")
            if self.step_config is not None:
                raise ValueError("parallel node must not define step_config")
            return self

        if self.node_type == WorkflowNodeType.CONDITION:
            if not self.condition_cel:
                raise ValueError("condition node requires condition_cel")
            if not self.true_steps:
                raise ValueError("condition node requires at least one true_steps entry")
            if self.children:
                raise ValueError("condition node must not use children; use true_steps/false_steps")
            if self.choices:
                raise ValueError("condition node must not define choices")
            if self.loop_config is not None:
                raise ValueError("condition node must not define loop_config")
            if self.step_config is not None:
                raise ValueError("condition node must not define step_config")
            # on_reject=else_branch routes a rejected gate
            # into the false branch at runtime;
            if (
                self.human_review is not None
                and self.human_review.on_reject == OnRejectPolicy.ELSE_BRANCH
                and not self.false_steps
            ):
                raise ValueError("condition node with on_reject=else_branch requires at least one false_steps entry")
            return self

        if self.node_type == WorkflowNodeType.LOOP:
            if not self.children:
                raise ValueError("loop node requires children")
            if self.loop_config is None:
                raise ValueError("loop node requires loop_config")
            if self.true_steps or self.false_steps:
                raise ValueError("loop node must not define true_steps/false_steps")
            if self.choices:
                raise ValueError("loop node must not define choices")
            if self.condition_cel is not None:
                raise ValueError("loop node must not define condition_cel")
            if self.step_config is not None:
                raise ValueError("loop node must not define step_config")
            return self

        if self.node_type == WorkflowNodeType.ROUTER:
            if not self.condition_cel:
                raise ValueError("router node requires condition_cel")
            if len(self.choices) < 2:
                raise ValueError("router node requires at least 2 choices")
            if self.children:
                raise ValueError("router node must not use children; use choices")
            if self.true_steps or self.false_steps:
                raise ValueError("router node must not define true_steps/false_steps")
            if self.loop_config is not None:
                raise ValueError("router node must not define loop_config")
            if self.step_config is not None:
                raise ValueError("router node must not define step_config")
            duplicate_names = [name for name, count in Counter(c.name for c in self.choices).items() if count > 1]
            if duplicate_names:
                raise ValueError("router choices must have unique names: " + ", ".join(sorted(duplicate_names)))
            return self
        raise ValueError(f"Unhandled node_type: {self.node_type!r}")


RouterChoice.model_rebuild()
WorkflowNode.model_rebuild()


def _validate_human_review_for_node(node_type: WorkflowNodeType, spec: HumanReviewSpec | None) -> None:
    """
        HITL: enforce agno's per-primitive HumanReview compatibility rules.

    Mirrors ``agno.workflow.types.validate_human_review_for_*`` so we fail fast
    at definition time instead of letting agno raise at compile time.
    """
    if spec is None:
        return

    # else_branch needs a false branch, which only CONDITION nodes have; agno only
    # fails on this at runtime (its validators ignore on_reject), so reject it here.
    if spec.on_reject == OnRejectPolicy.ELSE_BRANCH and node_type != WorkflowNodeType.CONDITION:
        raise ValueError("on_reject=else_branch is only supported on condition nodes")

    if node_type == WorkflowNodeType.PARALLEL:
        # agno itself raises ``requires_confirmation is not supported on Parallel``.
        if (
            spec.requires_confirmation
            or spec.requires_user_input
            or spec.requires_output_review
            or spec.requires_iteration_review
        ):
            raise ValueError("parallel node does not support any HITL fields (agno restriction)")
        return

    if node_type == WorkflowNodeType.STEP:
        if spec.requires_iteration_review:
            raise ValueError("requires_iteration_review is not supported on step nodes")
        return

    if node_type == WorkflowNodeType.ROUTER:
        if spec.requires_iteration_review:
            raise ValueError("requires_iteration_review is not supported on router nodes")
        return

    if node_type == WorkflowNodeType.LOOP:
        if spec.requires_user_input:
            raise ValueError("requires_user_input is not supported on loop nodes")
        if spec.requires_output_review:
            raise ValueError("requires_output_review is not supported on loop nodes")
        return

    if node_type == WorkflowNodeType.CONDITION:
        if spec.requires_user_input:
            raise ValueError("requires_user_input is not supported on condition nodes")
        if spec.requires_output_review:
            raise ValueError("requires_output_review is not supported on condition nodes")
        if spec.requires_iteration_review:
            raise ValueError("requires_iteration_review is not supported on condition nodes")
        # else_branch is only valid here (condition has true/false branches)
        return

    # Any future node type defaults to "no HITL until explicitly supported".
    if (
        spec.requires_confirmation
        or spec.requires_user_input
        or spec.requires_output_review
        or spec.requires_iteration_review
    ):
        raise ValueError(f"HITL not supported on node_type={node_type!r}")


class ResolvedDependency(BaseModel):
    """Resolution plan for a node when retrying a partially failed run."""

    node_id: str
    resolution: ResolvedDependencyResolution
    source_node_run_id: PydanticObjectId | None = None


def _collect_node_names_with_duplicates(nodes: list[WorkflowNode]) -> list[str]:
    """Depth-first collect of every node name, preserving duplicates for Counter-based uniqueness check."""
    names: list[str] = []
    for node in nodes:
        names.append(node.name)
        names.extend(_collect_node_names_with_duplicates(node.children))
        names.extend(_collect_node_names_with_duplicates(node.true_steps))
        names.extend(_collect_node_names_with_duplicates(node.false_steps))
        for choice in node.choices:
            names.extend(_collect_node_names_with_duplicates(choice.steps))
    return names


def _collect_all_node_names(nodes: list[WorkflowNode]) -> set[str]:
    """Recursively collect every node name in the workflow tree."""
    names: set[str] = set()
    for node in nodes:
        names.add(node.name)
        names.update(_collect_all_node_names(node.children))
        names.update(_collect_all_node_names(node.true_steps))
        names.update(_collect_all_node_names(node.false_steps))
        for choice in node.choices:
            names.update(_collect_all_node_names(choice.steps))
    return names


def _validate_references_exist(nodes: list[WorkflowNode], all_names: set[str]) -> None:
    """Raise ValueError if any step node references a name absent from the definition."""
    for node in nodes:
        if node.referenced_node_names:
            unknown = [n for n in node.referenced_node_names if n not in all_names]
            if unknown:
                raise ValueError(
                    f"node {node.name!r} references unknown node names {unknown}; available names: {sorted(all_names)}"
                )
        _validate_references_exist(node.children, all_names)
        _validate_references_exist(node.true_steps, all_names)
        _validate_references_exist(node.false_steps, all_names)
        for choice in node.choices:
            _validate_references_exist(choice.steps, all_names)


class WorkflowDefinition(Document):
    name: str
    description: str | None = None
    canvas: WorkflowCanvas = Field(default_factory=WorkflowCanvas)
    nodes: list[WorkflowNode] = Field(default_factory=list)
    enabled: bool = Field(default=False, description="Whether the workflow is enabled")
    version: int = 1
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("nodes")
    @classmethod
    def _validate_nodes_not_empty(cls, value: list[WorkflowNode]) -> list[WorkflowNode]:
        if not value:
            raise ValueError("workflow definition requires at least 1 root node")
        return value

    @model_validator(mode="after")
    def _validate_node_names_unique(self) -> WorkflowDefinition:
        """Every node name must be unique across the entire workflow tree.

        referenced_node_names resolution relies on one flat name→node map spanning
        the whole tree, so two nodes sharing a name in different branches are already
        ambiguous even if they never execute concurrently.
        """
        duplicates = sorted(
            name for name, count in Counter(_collect_node_names_with_duplicates(self.nodes)).items() if count > 1
        )
        if duplicates:
            raise ValueError(f"node names must be unique across the workflow; duplicates found: {duplicates}")
        return self

    @model_validator(mode="after")
    def _validate_referenced_node_names_exist(self) -> WorkflowDefinition:
        """Every name in referenced_node_names must match a node that exists in the definition."""
        all_names = _collect_all_node_names(self.nodes)
        _validate_references_exist(self.nodes, all_names)
        return self

    class Settings:
        name = "workflow_definitions"
        indexes = [
            [("name", ASCENDING)],
        ]


class WorkflowVersion(Document):
    """Historical snapshot of a WorkflowDefinition at a specific version.

    Written on each PUT before the definition is overwritten, so prior versions
    remain available for deterministic replay and audit.
    """

    workflow_id: PydanticObjectId
    version: int
    definition: dict[str, Any]
    checksum: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "workflow_versions"
        indexes = [
            IndexModel([("workflow_id", ASCENDING), ("version", ASCENDING)], unique=True),
        ]


class NodeRun(Document):
    """Execution record for a single node within a WorkflowRun."""

    workflow_run_id: PydanticObjectId
    node_id: str
    node_name: str
    status: NodeRunStatus = NodeRunStatus.PENDING
    attempt: int = 0
    input_snapshot: dict[str, Any] | None = None
    output_snapshot: dict[str, Any] | None = None
    session_state_snapshot: dict[str, Any] | None = None
    error: str | None = None
    selected_a2a_key: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    class Settings:
        name = "node_runs"
        indexes = [
            [("workflow_run_id", ASCENDING), ("node_id", ASCENDING)],
            "workflow_run_id",
        ]


class WorkflowRun(Document):
    """Top-level record for a single execution of a WorkflowDefinition."""

    workflow_definition_id: PydanticObjectId
    # Version of the definition this run executed against (snapshot in definition_snapshot).
    workflow_version: int | None = None
    status: WorkflowRunStatus = WorkflowRunStatus.PENDING
    trigger_source: str | None = None
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    initial_input: dict[str, Any] | None = None
    final_output: dict[str, Any] | None = None
    error_summary: str | None = None
    definition_snapshot: dict[str, Any] | None = None

    parent_run_id: PydanticObjectId | None = None
    resolved_dependencies: list[ResolvedDependency] = Field(default_factory=list)

    # Directive control fields — written by the API layer, read by the executor wrapper.
    # ``pending_directive`` is the MongoDB source of truth; the in-process asyncio.Queue
    # is the fast path.  On service restart the Queue is lost but this field survives,
    # allowing startup cleanup to mark orphan runs correctly.
    pending_directive: WorkflowDirective | None = None

    # agno generates its own internal run_id (UUID) inside ``workflow.arun``;
    agno_run_id: str | None = None
    # Set when the run transitions to PAUSED; used to enforce pause_timeout_seconds.
    paused_at: datetime | None = None
    # How long (seconds) a paused run may wait before being automatically cancelled.
    # NOTE: not yet enforced — ad-hoc PAUSE auto-cancel needs the periodic run
    # reaper (tracked separately). HITL *gate* timeouts (HumanReviewSpec.on_timeout)
    # ARE enforced lazily at continue-time; see WorkflowControlService.get_run_status.
    pause_timeout_seconds: int = 3600

    # HITL: serialized agno ``StepRequirement`` objects awaiting user decision.
    # Populated by ``WorkflowRunner._handle_run_output`` when ``arun()`` returns
    # ``is_paused=True``; cleared (rehydrated into agno) by ``continue_run``.
    # Atomically updated by ``WorkflowControlService.resolve_requirement`` using
    # MongoDB ``array_filters`` so concurrent decisions on different stepIds are safe.
    pending_requirements: list[dict[str, Any]] = Field(default_factory=list)

    # Non-sensitive identity of the triggering user, captured so an HITL resume
    # can re-mint a short-lived service JWT on their behalf. We deliberately do
    # NOT persist the raw bearer token — see _prepare_resume_credentials.
    triggering_user_id: str | None = None
    triggering_username: str | None = None
    triggering_scopes: list[str] | None = None

    class Settings:
        name = "workflow_runs"
        indexes = ["workflow_definition_id", "status", "parent_run_id"]
