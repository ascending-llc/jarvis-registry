from datetime import UTC, datetime

from registry.schemas.workflow_api_schemas import (
    PendingUserInputField,
    RouterChoiceInput,
    RouterChoiceOutput,
    StepRequirementSummary,
    WorkflowNodeInput,
    WorkflowNodeOutput,
    WorkflowRunDetailResponse,
)


def test_workflow_node_output_uses_independent_container_defaults():
    first = WorkflowNodeOutput(id="node-1", name="First", nodeType="step")
    second = WorkflowNodeOutput(id="node-2", name="Second", nodeType="step")

    first.config["key"] = "value"
    first.children.append(WorkflowNodeOutput(id="child-1", name="Child", nodeType="step"))

    assert second.config == {}
    assert second.children == []


def test_workflow_node_output_branch_fields_have_independent_defaults():
    """Guards against mutable-default-argument bugs on trueSteps/falseSteps/choices."""
    first = WorkflowNodeOutput(id="n1", name="First", nodeType="condition")
    second = WorkflowNodeOutput(id="n2", name="Second", nodeType="condition")

    first.trueSteps.append(WorkflowNodeOutput(id="t1", name="T1", nodeType="step"))
    first.falseSteps.append(WorkflowNodeOutput(id="f1", name="F1", nodeType="step"))
    first.choices.append(
        RouterChoiceOutput(
            name="c1",
            steps=[WorkflowNodeOutput(id="s1", name="S1", nodeType="step")],
        )
    )

    assert second.trueSteps == []
    assert second.falseSteps == []
    assert second.choices == []


def test_workflow_node_input_branch_fields_have_independent_defaults():
    first = WorkflowNodeInput(name="First", nodeType="condition")
    second = WorkflowNodeInput(name="Second", nodeType="condition")

    first.trueSteps.append(WorkflowNodeInput(name="T1", nodeType="step", executorKey="t"))
    first.falseSteps.append(WorkflowNodeInput(name="F1", nodeType="step", executorKey="f"))
    first.choices.append(
        RouterChoiceInput(
            name="c1",
            steps=[WorkflowNodeInput(name="S1", nodeType="step", executorKey="s")],
        )
    )

    assert second.trueSteps == []
    assert second.falseSteps == []
    assert second.choices == []


def test_router_choice_input_round_trip_with_multi_step():
    """RouterChoiceInput should accept and round-trip a multi-step pipeline."""
    choice = RouterChoiceInput(
        name="tech-path",
        steps=[
            WorkflowNodeInput(name="hn-research", nodeType="step", executorKey="hackernews-agent"),
            WorkflowNodeInput(name="deep-dive", nodeType="step", executorKey="analysis-agent"),
        ],
    )
    assert choice.name == "tech-path"
    assert [s.name for s in choice.steps] == ["hn-research", "deep-dive"]


def test_workflow_node_input_accepts_condition_with_multi_step_branches():
    """The API-level schema must accept the AS-1606 motivating example shape."""
    condition = WorkflowNodeInput(
        name="B",
        nodeType="condition",
        conditionCel="input.routeToTrue == true",
        trueSteps=[
            WorkflowNodeInput(name="C", nodeType="step", executorKey="tool-c"),
            WorkflowNodeInput(name="E", nodeType="step", executorKey="tool-e"),
            WorkflowNodeInput(name="G", nodeType="step", executorKey="tool-g"),
        ],
        falseSteps=[
            WorkflowNodeInput(name="D", nodeType="step", executorKey="tool-d"),
        ],
    )
    assert [s.name for s in condition.trueSteps] == ["C", "E", "G"]
    assert [s.name for s in condition.falseSteps] == ["D"]


def test_workflow_node_input_accepts_router_with_named_choices():
    router = WorkflowNodeInput(
        name="rt",
        nodeType="router",
        conditionCel="input.strategy",
        choices=[
            RouterChoiceInput(
                name="tech",
                steps=[
                    WorkflowNodeInput(name="hn", nodeType="step", executorKey="hn-tool"),
                    WorkflowNodeInput(name="deep", nodeType="step", executorKey="deep-tool"),
                ],
            ),
            RouterChoiceInput(
                name="general",
                steps=[WorkflowNodeInput(name="web", nodeType="step", executorKey="web-tool")],
            ),
        ],
    )
    assert [c.name for c in router.choices] == ["tech", "general"]
    assert [s.name for s in router.choices[0].steps] == ["hn", "deep"]
    assert [s.name for s in router.choices[1].steps] == ["web"]


def test_workflow_run_detail_response_uses_independent_list_defaults():
    first = WorkflowRunDetailResponse(
        id="run-1",
        workflowDefinitionId="workflow-1",
        status="pending",
        startedAt=datetime.now(UTC),
    )
    second = WorkflowRunDetailResponse(
        id="run-2",
        workflowDefinitionId="workflow-2",
        status="pending",
        startedAt=datetime.now(UTC),
    )

    first.nodeRuns.append(
        {
            "id": "node-run-1",
            "workflowRunId": "run-1",
            "nodeId": "node-1",
            "nodeName": "First",
            "status": "completed",
            "attempt": 1,
        }
    )

    assert second.resolvedDependencies == []
    assert second.nodeRuns == []


def _agno_requirement_dict(**overrides):
    """Mirror agno ``StepRequirement.to_dict()`` + serde envelope (snake_case keys)."""
    base = {
        "step_id": "step-1",
        "step_name": "Approve",
        "step_index": 0,
        "step_type": "step",
        "requires_confirmation": True,
        "confirmation_message": "Proceed?",
        "on_reject": "cancel",
        "requires_user_input": False,
        "requires_route_selection": False,
        "allow_multiple_selections": False,
        "requires_output_review": False,
        "is_post_execution": False,
        "retry_count": 0,
        "on_timeout": "cancel",
        "schema_version": 1,
    }
    base.update(overrides)
    return base


def test_step_requirement_summary_accepts_agno_snake_case_keys():
    summary = StepRequirementSummary.model_validate(_agno_requirement_dict())

    assert summary.stepId == "step-1"
    assert summary.schemaVersion == 1
    assert summary.requiresConfirmation is True

    # Serialization stays camelCase for the frontend contract.
    dumped = summary.model_dump()
    assert "stepId" in dumped
    assert "step_id" not in dumped


def test_step_requirement_summary_hydrates_pending_user_input_fields():
    payload = _agno_requirement_dict(
        requires_user_input=True,
        user_input_message="Enter your name",
        user_input_schema=[
            {
                "name": "fullName",
                "field_type": "str",
                "description": "Full legal name",
                "required": True,
                "value": None,
                "allowed_values": None,
            }
        ],
    )

    summary = StepRequirementSummary.model_validate(payload)

    assert summary.userInputSchema is not None
    field = summary.userInputSchema[0]
    assert isinstance(field, PendingUserInputField)
    assert field.name == "fullName"
    # agno runtime type name is passed through verbatim (not coerced to "string").
    assert field.fieldType == "str"
    assert field.required is True


def test_pending_user_input_field_round_trips_snake_to_camel():
    field = PendingUserInputField.model_validate(
        {"name": "age", "field_type": "int", "allowed_values": [18, 21], "required": False}
    )

    assert field.fieldType == "int"
    assert field.allowedValues == [18, 21]
    assert field.required is False

    dumped = field.model_dump()
    assert dumped["fieldType"] == "int"
    assert dumped["allowedValues"] == [18, 21]
    assert "field_type" not in dumped
