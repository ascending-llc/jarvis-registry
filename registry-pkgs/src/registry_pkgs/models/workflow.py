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

    @model_validator(mode="after")
    def _validate_shape(self) -> WorkflowNode:
        if self.node_type == WorkflowNodeType.STEP:
            if not self.executor_key:
                raise ValueError("step node requires executor_key")
            if self.children:
                raise ValueError("step node must not have children")
            if self.condition_cel is not None:
                raise ValueError("step node must not define condition_cel")
            if self.loop_config is not None:
                raise ValueError("step node must not define loop_config")
            return self

        if self.executor_key is not None:
            raise ValueError(f"{self.node_type} node must not define executor_key")
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
