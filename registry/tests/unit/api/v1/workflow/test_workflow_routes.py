from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId
from fastapi import HTTPException, Request

from registry.api.v1.workflow import workflow_routes
from registry.schemas.acl_schema import ResourcePermissions
from registry.schemas.workflow_api_schemas import WorkflowCreateRequest


def _request_with_headers(headers: dict[str, str]) -> Request:
    request = MagicMock(spec=Request)
    request.headers = headers
    return request


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

    from registry_pkgs.models.workflow import WorkflowDefinition, WorkflowNode

    fake_workflow = WorkflowDefinition.model_construct(
        id="wf-demo-id",
        name="Demo",
        description=None,
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

    mock_acl = MagicMock()
    mock_acl.grant_permission = AsyncMock()

    user_context = {
        "user_id": str(PydanticObjectId()),
        "username": "tester",
        "groups": [],
        "scopes": ["workflow:create"],
    }

    response = await workflow_routes.create_workflow(
        data=request,
        user_context=user_context,
        workflow_service=mock_service,
        acl_service=mock_acl,
    )

    mock_service.create_workflow.assert_awaited_once()
    mock_acl.grant_permission.assert_awaited_once()
    grant_kwargs = mock_acl.grant_permission.await_args.kwargs
    assert grant_kwargs["resource_type"].value == "workflow"
    assert grant_kwargs["perm_bits"] == 15  # OWNER
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


def _fake_workflow(name: str = "wf", version: int = 1):
    from registry_pkgs.models.workflow import WorkflowDefinition, WorkflowNode

    return WorkflowDefinition.model_construct(
        id=PydanticObjectId(),
        name=name,
        description=None,
        nodes=[WorkflowNode(name="s", node_type="step", executor_key="tool")],
        version=version,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_list_workflows_filters_by_accessible_ids():
    """list_workflows must restrict results to the caller's ACL-accessible IDs."""
    wf = _fake_workflow()
    user_context = {"user_id": str(PydanticObjectId()), "username": "u", "groups": [], "scopes": []}

    mock_service = MagicMock()
    mock_service.list_workflows = AsyncMock(return_value=([wf], 1))

    mock_acl = MagicMock()
    mock_acl.get_accessible_resource_ids = AsyncMock(return_value=[str(wf.id)])
    mock_acl.get_user_permissions_for_resource = AsyncMock(return_value=ResourcePermissions(VIEW=True))

    response = await workflow_routes.list_workflows(
        user_context=user_context,
        workflow_service=mock_service,
        acl_service=mock_acl,
    )

    mock_acl.get_accessible_resource_ids.assert_awaited_once()
    forwarded = mock_service.list_workflows.await_args.kwargs
    assert forwarded["accessible_workflow_ids"] == [str(wf.id)]
    assert len(response.workflows) == 1
    assert response.workflows[0].aclPermission.VIEW is True


@pytest.mark.asyncio
async def test_get_workflow_propagates_403_without_view():
    user_context = {"user_id": str(PydanticObjectId()), "username": "u", "groups": [], "scopes": []}

    mock_service = MagicMock()
    mock_service.get_workflow_by_id = AsyncMock(return_value=_fake_workflow())

    mock_acl = MagicMock()
    mock_acl.check_user_permission = AsyncMock(side_effect=HTTPException(status_code=403, detail="no view"))

    with pytest.raises(HTTPException) as exc_info:
        await workflow_routes.get_workflow(
            workflow_id=str(PydanticObjectId()),
            user_context=user_context,
            workflow_service=mock_service,
            acl_service=mock_acl,
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_list_workflow_versions_returns_history():
    user_context = {"user_id": str(PydanticObjectId()), "username": "u", "groups": [], "scopes": []}

    mock_service = MagicMock()
    mock_service.get_workflow_by_id = AsyncMock(return_value=_fake_workflow())
    mock_service.list_versions = AsyncMock(
        return_value=[
            {"version": 1, "created_at": datetime.now(UTC), "checksum": "aaa"},
            {"version": 2, "created_at": datetime.now(UTC), "checksum": "bbb"},
        ]
    )

    mock_acl = MagicMock()
    mock_acl.check_user_permission = AsyncMock(return_value=ResourcePermissions(VIEW=True))

    response = await workflow_routes.list_workflow_versions(
        workflow_id=str(PydanticObjectId()),
        user_context=user_context,
        workflow_service=mock_service,
        acl_service=mock_acl,
    )

    assert [v.version for v in response.versions] == [1, 2]
    assert response.versions[1].checksum == "bbb"


@pytest.mark.asyncio
async def test_trigger_run_forwards_requested_version(monkeypatch):
    from types import SimpleNamespace

    from registry.schemas.workflow_api_schemas import WorkflowRunTriggerRequest
    from registry_pkgs.models.enums import WorkflowRunStatus

    monkeypatch.setattr(workflow_routes, "generate_service_jwt", MagicMock(return_value="svc-token"))

    user_context = {"user_id": str(PydanticObjectId()), "username": "u", "groups": [], "scopes": []}

    run = SimpleNamespace(
        id=PydanticObjectId(),
        workflow_definition_id=PydanticObjectId(),
        status=WorkflowRunStatus.PENDING,
        trigger_source="api",
        started_at=datetime.now(UTC),
    )

    mock_service = MagicMock()
    mock_service.get_workflow_by_id = AsyncMock(return_value=_fake_workflow(version=3))
    mock_service.trigger_workflow_run = AsyncMock(return_value=run)

    mock_acl = MagicMock()
    mock_acl.check_user_permission = AsyncMock(return_value=ResourcePermissions(VIEW=True))

    request = MagicMock(spec=Request)
    request.headers = {"Authorization": "Bearer abc"}
    background_tasks = MagicMock()

    await workflow_routes.trigger_workflow_run(
        workflow_id=str(PydanticObjectId()),
        data=WorkflowRunTriggerRequest(version=2),
        background_tasks=background_tasks,
        user_context=user_context,
        request=request,
        workflow_service=mock_service,
        workflow_runner=MagicMock(),
        acl_service=mock_acl,
    )

    assert mock_service.trigger_workflow_run.await_args.kwargs["version"] == 2
    background_tasks.add_task.assert_called_once()


@pytest.mark.asyncio
async def test_trigger_run_forbidden_without_view():
    """POST /runs requires VIEWER on the workflow; missing VIEW → 403 before triggering."""
    from registry.schemas.workflow_api_schemas import WorkflowRunTriggerRequest

    user_context = {"user_id": str(PydanticObjectId()), "username": "u", "groups": [], "scopes": []}

    mock_service = MagicMock()
    mock_service.get_workflow_by_id = AsyncMock(return_value=_fake_workflow())
    mock_service.trigger_workflow_run = AsyncMock()

    mock_acl = MagicMock()
    mock_acl.check_user_permission = AsyncMock(side_effect=HTTPException(status_code=403, detail="no view"))

    request = MagicMock(spec=Request)
    request.headers = {"Authorization": "Bearer abc"}

    with pytest.raises(HTTPException) as exc_info:
        await workflow_routes.trigger_workflow_run(
            workflow_id=str(PydanticObjectId()),
            data=WorkflowRunTriggerRequest(),
            background_tasks=MagicMock(),
            user_context=user_context,
            request=request,
            workflow_service=mock_service,
            workflow_runner=MagicMock(),
            acl_service=mock_acl,
        )

    assert exc_info.value.status_code == 403
    mock_service.trigger_workflow_run.assert_not_awaited()


def test_list_runs_status_filter_covers_all_run_statuses():
    """The list_workflow_runs status filter must accept every WorkflowRunStatus
    value (including awaiting_approval) so runs in any state can be filtered."""
    import inspect
    import typing

    from registry_pkgs.models.enums import WorkflowRunStatus

    annotation = inspect.signature(workflow_routes.list_workflow_runs).parameters["status"].annotation
    # Annotated[Literal[...] | None, Query()] → first arg is the `Literal[...] | None` union
    union = typing.get_args(annotation)[0]
    literal_values: set[str] = set()
    for member in typing.get_args(union):
        literal_values |= set(typing.get_args(member))  # Literal members; NoneType yields ()

    assert {s.value for s in WorkflowRunStatus} <= literal_values


def test_workflow_create_request_parses_approval_fields():
    request = WorkflowCreateRequest.model_validate(
        {
            "name": "Approval Demo",
            "nodes": [
                {
                    "name": "gate",
                    "nodeType": "step",
                    "executorKey": "tool",
                    "requireApproval": True,
                    "approvalTimeoutSeconds": 120,
                }
            ],
        }
    )

    node = request.nodes[0]
    assert node.requireApproval is True
    assert node.approvalTimeoutSeconds == 120
