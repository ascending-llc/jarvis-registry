from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from beanie import Document, PydanticObjectId
from pydantic import BaseModel, Field, field_validator, model_validator
from pymongo import ASCENDING

from registry_pkgs.models.enums import (
    NodeRunStatus,
    ResolvedDependencyResolution,
    WorkflowNodeType,
    WorkflowRunStatus,
)


class StepConfig(BaseModel):
    """Per-step execution controls for STEP nodes.

    Fields map directly to agno ``Step`` parameters so the workflow engine
    honours them at runtime without any extra translation layer.

    Attributes:
        max_retries:  How many times to retry the step on failure (0 = no retry).
        on_error:     What to do when the step ultimately fails after all retries.
                      ``"fail"`` aborts the whole workflow run;
                      ``"skip"`` records the failure and continues to the next step.
    """

    max_retries: int = 0
    on_error: str = "fail"

    @field_validator("max_retries")
    @classmethod
    def _validate_max_retries(cls, value: int) -> int:
        if value < 0:
            raise ValueError("max_retries must be >= 0")
        return value

    @field_validator("on_error")
    @classmethod
    def _validate_on_error(cls, value: str) -> str:
        allowed = {"fail", "skip"}
        if value not in allowed:
            raise ValueError(f"on_error must be one of {sorted(allowed)}, got {value!r}")
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

    # Child nodes for container nodes (parallel / loop / condition / router)
    children: list[WorkflowNode] = Field(default_factory=list)
    # Node Branch
    # CEL expression used by condition / router nodes.
    # Condition: returns bool; available variables: input, previous_step_content,
    #            previous_step_outputs, additional_data, session_state
    # Router:    returns a step name string; additional variable:
    #            step_choices (list of all child step names)
    condition_cel: str | None = None
    loop_config: LoopConfig | None = None

    @field_validator("a2a_pool")
    @classmethod
    def _validate_a2a_pool(cls, value: list[str]) -> list[str]:
        if len(value) > 5:
            raise ValueError("a2a_pool must contain at most 5 agents")
        return value

    @model_validator(mode="after")
    def _validate_shape(self) -> WorkflowNode:
        if self.node_type == WorkflowNodeType.STEP:
            has_key = bool(self.executor_key)
            has_pool = bool(self.a2a_pool)
            if not has_key and not has_pool:
                raise ValueError("step node requires either executor_key or a2a_pool")
            if has_key and has_pool:
                raise ValueError("step node must not define both executor_key and a2a_pool")
            if self.children:
                raise ValueError("step node must not have children")
            if self.condition_cel is not None:
                raise ValueError("step node must not define condition_cel")
            if self.loop_config is not None:
                raise ValueError("step node must not define loop_config")
            return self

        if self.executor_key is not None or self.a2a_pool:
            raise ValueError(f"{self.node_type} node must not define executor_key or a2a_pool")
        if self.step_config is not None:
            raise ValueError(f"{self.node_type} node must not define step_config")
        if not self.children:
            raise ValueError(f"{self.node_type} node requires children")

        if self.node_type == WorkflowNodeType.PARALLEL:
            if len(self.children) < 2:
                raise ValueError("parallel node requires at least 2 children")
            if self.condition_cel is not None:
                raise ValueError("parallel node must not define condition_cel")
            if self.loop_config is not None:
                raise ValueError("parallel node must not define loop_config")
            return self

        if self.node_type == WorkflowNodeType.CONDITION:
            if not self.condition_cel:
                raise ValueError("condition node requires condition_cel")
            if len(self.children) not in (1, 2):
                raise ValueError("condition node requires 1 or 2 children")
            if self.loop_config is not None:
                raise ValueError("condition node must not define loop_config")
            return self

        if self.node_type == WorkflowNodeType.LOOP:
            if self.loop_config is None:
                raise ValueError("loop node requires loop_config")
            if self.condition_cel is not None:
                raise ValueError("loop node must not define condition_cel")
            return self

        if self.node_type == WorkflowNodeType.ROUTER:
            if not self.condition_cel:
                raise ValueError("router node requires condition_cel")
            if len(self.children) < 2:
                raise ValueError("router node requires at least 2 children")
            if self.loop_config is not None:
                raise ValueError("router node must not define loop_config")
            duplicate_names = [name for name, count in Counter(c.name for c in self.children).items() if count > 1]
            if duplicate_names:
                raise ValueError("router node children must have unique names: " + ", ".join(sorted(duplicate_names)))
            return self
        raise ValueError(f"Unhandled node_type: {self.node_type!r}")


WorkflowNode.model_rebuild()


class ResolvedDependency(BaseModel):
    """Resolution plan for a node when retrying a partially failed run."""

    node_id: str
    resolution: ResolvedDependencyResolution
    source_node_run_id: PydanticObjectId | None = None


class WorkflowDefinition(Document):
    name: str
    description: str | None = None
    nodes: list[WorkflowNode] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("nodes")
    @classmethod
    def _validate_nodes_not_empty(cls, value: list[WorkflowNode]) -> list[WorkflowNode]:
        if not value:
            raise ValueError("workflow definition requires at least 1 root node")
        return value

    class Settings:
        name = "workflow_definitions"
        indexes = [
            [("name", ASCENDING)],
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
    selected_a2a_key: str | None = None
    error: str | None = None
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

    class Settings:
        name = "workflow_runs"
        indexes = ["workflow_definition_id", "status", "parent_run_id"]
