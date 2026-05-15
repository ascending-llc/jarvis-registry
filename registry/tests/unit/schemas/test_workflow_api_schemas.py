from datetime import UTC, datetime

from registry.schemas.workflow_api_schemas import WorkflowNodeOutput, WorkflowRunDetailResponse


def test_workflow_node_output_uses_independent_container_defaults():
    first = WorkflowNodeOutput(id="node-1", name="First", nodeType="step")
    second = WorkflowNodeOutput(id="node-2", name="Second", nodeType="step")

    first.config["key"] = "value"
    first.children.append(WorkflowNodeOutput(id="child-1", name="Child", nodeType="step"))

    assert second.config == {}
    assert second.children == []


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
