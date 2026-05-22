from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, Request

from registry.api.v1.workflow import workflow_routes
from registry.schemas.workflow_api_schemas import WorkflowCreateRequest


def _request_with_headers(headers: dict[str, str]) -> Request:
    request = MagicMock(spec=Request)
    request.headers = headers
    return request


def _canvas() -> dict[str, dict[str, float]]:
    return {"viewport": {"x": 0, "y": 0, "zoom": 1}}


def test_build_registry_token_prefers_authorization_header(monkeypatch: pytest.MonkeyPatch):
    generate_service_jwt = MagicMock(return_value="generated-token")
    monkeypatch.setattr(workflow_routes, "generate_service_jwt", generate_service_jwt)

    token = workflow_routes._build_registry_token(
        _request_with_headers({"Authorization": "Bearer header-token"}),
        {
            "user_id": "user-1",
            "username": "testuser",
            "groups": [],
            "scopes": ["workflow:run"],
            "auth_method": "jwt",
            "provider": "jwt",
            "auth_source": "jwt_auth",
        },
    )

    assert token == "header-token"
    generate_service_jwt.assert_not_called()


def test_build_registry_token_generates_service_jwt_without_authorization_header(monkeypatch: pytest.MonkeyPatch):
    generate_service_jwt = MagicMock(return_value="generated-token")
    monkeypatch.setattr(workflow_routes, "generate_service_jwt", generate_service_jwt)

    token = workflow_routes._build_registry_token(
        _request_with_headers({}),
        {
            "user_id": "user-1",
            "username": "testuser",
            "groups": [],
            "scopes": ["workflow:run"],
            "auth_method": "traditional",
            "provider": "local",
            "auth_source": "jwt_session_auth",
        },
    )

    assert token == "generated-token"
    generate_service_jwt.assert_called_once_with(
        user_id="user-1",
        username="testuser",
        scopes=["workflow:run"],
    )


def test_build_registry_token_requires_user_id_without_authorization_header():
    with pytest.raises(HTTPException) as exc_info:
        workflow_routes._build_registry_token(
            _request_with_headers({}),
            {
                "user_id": None,
                "username": "testuser",
                "groups": [],
                "scopes": ["workflow:run"],
                "auth_method": "traditional",
                "provider": "local",
                "auth_source": "jwt_session_auth",
            },
        )

    assert exc_info.value.status_code == 401


def test_workflow_create_request_deserializes_motivating_example_from_json():
    """Simulates FastAPI's JSON body parsing for the AS-1606 motivating example
    (A → B[CONDITION] true: C→E→G, false: D→F→H). Confirms camelCase field names
    (trueSteps/falseSteps) survive the HTTP-body deserialization layer intact."""
    json_body = {
        "name": "Tree-Shaped Workflow",
        "canvas": _canvas(),
        "nodes": [
            {"name": "A", "nodeType": "step", "executorKey": "tool-a"},
            {
                "name": "B",
                "nodeType": "condition",
                "conditionCel": "input.routeToTrue == true",
                "trueSteps": [
                    {"name": "C", "nodeType": "step", "executorKey": "tool-c"},
                    {"name": "E", "nodeType": "step", "executorKey": "tool-e"},
                    {"name": "G", "nodeType": "step", "executorKey": "tool-g"},
                ],
                "falseSteps": [
                    {"name": "D", "nodeType": "step", "executorKey": "tool-d"},
                    {"name": "F", "nodeType": "step", "executorKey": "tool-f"},
                    {"name": "H", "nodeType": "step", "executorKey": "tool-h"},
                ],
            },
        ],
    }

    request = WorkflowCreateRequest.model_validate(json_body)

    assert len(request.nodes) == 2
    assert request.nodes[0].name == "A"
    cond = request.nodes[1]
    assert cond.nodeType == "condition"
    assert cond.conditionCel == "input.routeToTrue == true"
    assert [n.name for n in cond.trueSteps] == ["C", "E", "G"]
    assert [n.name for n in cond.falseSteps] == ["D", "F", "H"]


def test_workflow_create_request_deserializes_router_with_named_choices_from_json():
    json_body = {
        "name": "Router Workflow",
        "canvas": _canvas(),
        "nodes": [
            {
                "name": "research-router",
                "nodeType": "router",
                "conditionCel": "input.strategy",
                "choices": [
                    {
                        "name": "tech",
                        "steps": [
                            {"name": "hn", "nodeType": "step", "executorKey": "hn-tool"},
                            {"name": "deep", "nodeType": "step", "executorKey": "deep-tool"},
                        ],
                    },
                    {
                        "name": "general",
                        "steps": [
                            {"name": "web", "nodeType": "step", "executorKey": "web-tool"},
                        ],
                    },
                ],
            }
        ],
    }

    request = WorkflowCreateRequest.model_validate(json_body)

    router = request.nodes[0]
    assert router.nodeType == "router"
    assert [c.name for c in router.choices] == ["tech", "general"]
    assert [s.name for s in router.choices[0].steps] == ["hn", "deep"]
    assert [s.name for s in router.choices[1].steps] == ["web"]


@pytest.mark.asyncio
async def test_create_workflow_route_forwards_condition_request_to_service():
    """End-to-end route → service contract: the create_workflow handler must hand
    the parsed WorkflowCreateRequest (with new trueSteps/falseSteps fields intact)
    to workflow_service.create_workflow, then return the converted detail response."""
    request = WorkflowCreateRequest.model_validate(
        {
            "name": "Demo",
            "canvas": _canvas(),
            "nodes": [
                {
                    "name": "B",
                    "nodeType": "condition",
                    "conditionCel": "input.x",
                    "trueSteps": [{"name": "C", "nodeType": "step", "executorKey": "tool-c"}],
                    "falseSteps": [{"name": "D", "nodeType": "step", "executorKey": "tool-d"}],
                }
            ],
        }
    )

    # Fake WorkflowDefinition stand-in for convert_to_detail
    from datetime import UTC, datetime

    from registry_pkgs.models.workflow import WorkflowCanvas, WorkflowDefinition, WorkflowNode

    fake_workflow = WorkflowDefinition.model_construct(
        id="wf-demo-id",
        name="Demo",
        description=None,
        canvas=WorkflowCanvas.model_validate(_canvas()),
        nodes=[
            WorkflowNode(
                name="B",
                node_type="condition",
                condition_cel="input.x",
                true_steps=[WorkflowNode(name="C", node_type="step", executor_key="tool-c")],
                false_steps=[WorkflowNode(name="D", node_type="step", executor_key="tool-d")],
            )
        ],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    mock_service = MagicMock()
    mock_service.create_workflow = AsyncMock(return_value=fake_workflow)

    user_context = {
        "user_id": "u1",
        "username": "tester",
        "groups": [],
        "scopes": ["workflow:create"],
    }

    response = await workflow_routes.create_workflow(
        data=request,
        user_context=user_context,
        workflow_service=mock_service,
    )

    mock_service.create_workflow.assert_awaited_once()
    forwarded = mock_service.create_workflow.await_args.kwargs["data"]
    cond = forwarded.nodes[0]
    assert cond.nodeType == "condition"
    assert [n.name for n in cond.trueSteps] == ["C"]
    assert [n.name for n in cond.falseSteps] == ["D"]

    # Response is the converted detail; verify the new fields round-trip
    assert response.name == "Demo"
    detail_cond = response.nodes[0]
    assert detail_cond.nodeType == "condition"
    assert [n.name for n in detail_cond.trueSteps] == ["C"]
    assert [n.name for n in detail_cond.falseSteps] == ["D"]
