from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beanie import PydanticObjectId
from fastapi import HTTPException, Request

from registry.api.v1.workflow import token_helpers, workflow_routes
from registry.schemas.acl_schema import ResourcePermissions
from registry.schemas.workflow_api_schemas import WorkflowCreateRequest, WorkflowUpdateRequest


def _request_with_headers(headers: dict[str, str]) -> Request:
    request = MagicMock(spec=Request)
    request.headers = headers
    return request


def _canvas() -> dict[str, dict[str, float]]:
    return {"viewport": {"x": 0, "y": 0, "zoom": 1}}


def test_build_registry_token_prefers_authorization_header(monkeypatch: pytest.MonkeyPatch):
    generate_service_jwt = MagicMock(return_value="generated-token")
    monkeypatch.setattr(token_helpers, "generate_service_jwt", generate_service_jwt)

    token = token_helpers.build_registry_token(
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
    monkeypatch.setattr(token_helpers, "generate_service_jwt", generate_service_jwt)

    token = token_helpers.build_registry_token(
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
        token_helpers.build_registry_token(
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


@pytest.mark.asyncio
async def test_update_workflow_passes_session_to_service(monkeypatch: pytest.MonkeyPatch):
    """update_workflow must open an explicit transaction and pass session=mongo_session
    to workflow_service.update_workflow."""
    workflow_id = str(PydanticObjectId())
    mock_updated_workflow = MagicMock()

    workflow_service = MagicMock()
    workflow_service.update_workflow = AsyncMock(return_value=mock_updated_workflow)
    acl_service = MagicMock()

    user_context = {"user_id": str(PydanticObjectId()), "username": "tester"}

    monkeypatch.setattr(
        workflow_routes,
        "_authorize_workflow",
        AsyncMock(return_value=(MagicMock(), MagicMock())),
    )

    mock_session = AsyncMock()
    mock_client = MagicMock()
    mock_client.start_session.return_value.__aenter__.return_value = mock_session
    mock_session.start_transaction.return_value.__aenter__.return_value = None

    data = WorkflowUpdateRequest(name="Updated")

    with (
        patch("registry.api.v1.workflow.workflow_routes.MongoDB.get_client", return_value=mock_client),
        patch("registry.api.v1.workflow.workflow_routes.convert_to_detail", return_value=MagicMock()),
    ):
        await workflow_routes.update_workflow(
            workflow_id=workflow_id,
            data=data,
            user_context=user_context,
            workflow_service=workflow_service,
            acl_service=acl_service,
        )

    workflow_service.update_workflow.assert_awaited_once_with(
        workflow_id=workflow_id,
        data=data,
        session=mock_session,
    )


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
    """List_workflows must restrict results to the caller's ACL-accessible IDs."""
    wf = _fake_workflow()
    user_context = {"user_id": str(PydanticObjectId()), "username": "u", "groups": [], "scopes": []}

    mock_service = MagicMock()
    mock_service.list_workflows = AsyncMock(return_value=([wf], 1))

    mock_acl = MagicMock()
    mock_acl.get_accessible_resource_ids = AsyncMock(return_value=[str(wf.id)])
    mock_acl.get_user_permissions_for_resources = AsyncMock(return_value={wf.id: ResourcePermissions(VIEW=True)})

    response = await workflow_routes.list_workflows(
        user_context=user_context,
        workflow_service=mock_service,
        acl_service=mock_acl,
    )

    mock_acl.get_accessible_resource_ids.assert_awaited_once()
    # Permissions resolved via a single batch query (no N+1).
    mock_acl.get_user_permissions_for_resources.assert_awaited_once()
    batch_kwargs = mock_acl.get_user_permissions_for_resources.await_args.kwargs
    assert batch_kwargs["resource_ids"] == [wf.id]
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

    monkeypatch.setattr(token_helpers, "generate_service_jwt", MagicMock(return_value="svc-token"))

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


def test_workflow_create_request_parses_human_review():
    """``humanReview`` embedded object mirrors agno's HumanReview dataclass 1:1.

    See ``registry_pkgs.models.workflow.HumanReviewSpec``.
    """
    request = WorkflowCreateRequest.model_validate(
        {
            "name": "Approval Demo",
            "canvas": _canvas(),
            "nodes": [
                {
                    "name": "gate",
                    "nodeType": "step",
                    "executorKey": "tool",
                    "humanReview": {
                        "requiresConfirmation": True,
                        "confirmationMessage": "Proceed?",
                        "onReject": "skip",
                        "timeoutSeconds": 120,
                        "onTimeout": "cancel",
                    },
                }
            ],
        }
    )

    node = request.nodes[0]
    assert node.humanReview is not None
    assert node.humanReview.requiresConfirmation is True
    assert node.humanReview.timeoutSeconds == 120
    assert node.humanReview.onReject.value == "skip"
    assert node.humanReview.onTimeout.value == "cancel"


@pytest.mark.asyncio
async def test_get_workflow_run_status_returns_run_status_response():
    """GET /runs/{run_id}/status must return a RunStatusResponse with per-node summary."""
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from registry.api.v1.workflow.workflow_routes import get_workflow_run_status
    from registry.schemas.workflow_schemas import RunStatusResponse
    from registry_pkgs.models.enums import WorkflowRunStatus

    wf_id = str(PydanticObjectId())
    run_id = str(PydanticObjectId())
    user_id = str(PydanticObjectId())

    run = SimpleNamespace(
        id=PydanticObjectId(run_id),
        workflow_definition_id=PydanticObjectId(wf_id),
        status=WorkflowRunStatus.RUNNING,
        trigger_source="api",
        started_at=datetime.now(UTC),
        finished_at=None,
        paused_at=None,
        error_summary=None,
        parent_run_id=None,
        pending_requirements=[],
    )

    node_run = SimpleNamespace(
        node_id="n1",
        node_name="step-1",
        status=WorkflowRunStatus.COMPLETED,
        attempt=1,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        error=None,
    )

    mock_control = MagicMock()
    mock_control.get_run_status = AsyncMock(return_value=(run, [node_run]))

    mock_acl = MagicMock()
    mock_acl.check_user_permission = AsyncMock(return_value=None)

    user_context = {"user_id": user_id, "username": "u", "groups": [], "scopes": []}

    response = await get_workflow_run_status(
        workflow_id=wf_id,
        run_id=run_id,
        user_context=user_context,
        workflow_control_service=mock_control,
        acl_service=mock_acl,
    )

    assert isinstance(response, RunStatusResponse)
    assert response.run_id == run_id
    assert response.workflow_id == wf_id
    assert response.status == WorkflowRunStatus.RUNNING.value
    assert len(response.node_runs) == 1
    assert response.node_runs[0].node_id == "n1"
    mock_control.get_run_status.assert_awaited_once_with(wf_id, run_id)


@pytest.mark.asyncio
async def test_get_workflow_run_status_nudges_continue_run_on_expired_requirement(monkeypatch):
    """The status endpoint must trigger the lazy timeout nudge when a pending
    requirement has passed its deadline."""
    import asyncio
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    from registry.api.v1.workflow.workflow_routes import get_workflow_run_status
    from registry.services import workflow_control_service as wcs_module
    from registry_pkgs.models.enums import WorkflowRunStatus
    from registry_pkgs.workflows.control import DirectiveQueue

    wf_id = str(PydanticObjectId())
    run_id = str(PydanticObjectId())
    user_id = str(PydanticObjectId())
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()

    run = SimpleNamespace(
        id=PydanticObjectId(run_id),
        workflow_definition_id=PydanticObjectId(wf_id),
        status=WorkflowRunStatus.AWAITING_APPROVAL,
        trigger_source="api",
        started_at=datetime.now(UTC),
        finished_at=None,
        paused_at=None,
        error_summary=None,
        parent_run_id=None,
        pending_requirements=[{"step_id": "s1", "confirmed": None, "timeout_at": past}],
        triggering_user_id="user-1",
        triggering_username="u",
        triggering_scopes=[],
    )

    fake_node_run = MagicMock()
    fake_node_run.find.return_value.to_list = AsyncMock(return_value=[])
    monkeypatch.setattr(wcs_module, "NodeRun", fake_node_run)
    monkeypatch.setattr(wcs_module, "generate_service_jwt", MagicMock(return_value="svc"))

    continue_mock = AsyncMock()
    service = wcs_module.WorkflowControlService(
        directive_queue=DirectiveQueue(),
        runner_factory=lambda: SimpleNamespace(continue_run=continue_mock),
    )
    service._load_run = AsyncMock(return_value=run)

    mock_acl = MagicMock()
    mock_acl.check_user_permission = AsyncMock(return_value=None)

    user_context = {"user_id": user_id, "username": "u", "groups": [], "scopes": []}

    await get_workflow_run_status(
        workflow_id=wf_id,
        run_id=run_id,
        user_context=user_context,
        workflow_control_service=service,
        acl_service=mock_acl,
    )
    await asyncio.sleep(0)  # let the fire-and-forget resume settle

    continue_mock.assert_awaited_once()
    assert continue_mock.await_args.kwargs["existing_run_id"] == run_id


def test_workflow_create_request_parses_human_review_with_retry():
    """``onReject: 'retry'`` must round-trip through the create request schema."""
    request = WorkflowCreateRequest.model_validate(
        {
            "name": "Retry Demo",
            "canvas": _canvas(),
            "nodes": [
                {
                    "name": "gate",
                    "nodeType": "step",
                    "executorKey": "tool",
                    "humanReview": {
                        "requiresConfirmation": True,
                        "confirmationMessage": "Proceed?",
                        "onReject": "retry",
                        "timeoutSeconds": 60,
                        "onTimeout": "cancel",
                    },
                }
            ],
        }
    )

    node = request.nodes[0]
    assert node.humanReview is not None
    assert node.humanReview.onReject.value == "retry"


@pytest.mark.asyncio
async def test_list_node_runs_returns_node_run_details():
    from types import SimpleNamespace

    from registry.api.v1.workflow.workflow_routes import list_node_runs
    from registry.schemas.workflow_schemas import NodeRunListResponse
    from registry_pkgs.models.enums import WorkflowRunStatus

    wf_id = str(PydanticObjectId())
    run_id = str(PydanticObjectId())
    nr_id = PydanticObjectId()

    fake_run = SimpleNamespace(id=PydanticObjectId(run_id))
    fake_nr = SimpleNamespace(
        id=nr_id,
        node_id="n1",
        node_name="step-1",
        workflow_run_id=PydanticObjectId(run_id),
        status=WorkflowRunStatus.COMPLETED,
        attempt=1,
        input_snapshot={"x": 1},
        output_snapshot={"y": 2},
        error=None,
        started_at=None,
        finished_at=None,
    )

    mock_service = MagicMock()
    mock_service.get_workflow_by_id = AsyncMock(return_value=MagicMock(id=PydanticObjectId(wf_id)))
    mock_service.get_workflow_run = AsyncMock(return_value=(fake_run, [fake_nr]))

    mock_acl = MagicMock()
    mock_acl.check_user_permission = AsyncMock(return_value=None)

    user_context = {"user_id": str(PydanticObjectId()), "username": "u", "groups": [], "scopes": []}

    response = await list_node_runs(
        workflow_id=wf_id,
        run_id=run_id,
        user_context=user_context,
        workflow_service=mock_service,
        acl_service=mock_acl,
    )

    assert isinstance(response, NodeRunListResponse)
    assert response.runId == run_id
    assert response.workflowId == wf_id
    assert len(response.nodeRuns) == 1
    assert response.nodeRuns[0].id == str(nr_id)
    assert response.nodeRuns[0].nodeId == "n1"
    assert response.nodeRuns[0].inputSnapshot == {"x": 1}
    assert response.nodeRuns[0].outputSnapshot == {"y": 2}
    mock_service.get_workflow_run.assert_awaited_once_with(workflow_id=wf_id, run_id=run_id)


@pytest.mark.asyncio
async def test_list_node_runs_forbidden_without_view():
    from registry.api.v1.workflow.workflow_routes import list_node_runs

    mock_acl = MagicMock()
    mock_acl.check_user_permission = AsyncMock(side_effect=HTTPException(status_code=403, detail="Forbidden"))

    mock_service = MagicMock()
    mock_service.get_workflow_by_id = AsyncMock(return_value=MagicMock())

    user_context = {"user_id": str(PydanticObjectId()), "username": "u", "groups": [], "scopes": []}

    with pytest.raises(HTTPException) as exc_info:
        await list_node_runs(
            workflow_id=str(PydanticObjectId()),
            run_id=str(PydanticObjectId()),
            user_context=user_context,
            workflow_service=mock_service,
            acl_service=mock_acl,
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_list_node_runs_run_not_found_returns_404():
    from registry.api.v1.workflow.workflow_routes import list_node_runs

    wf_id = str(PydanticObjectId())
    run_id = str(PydanticObjectId())

    mock_service = MagicMock()
    mock_service.get_workflow_by_id = AsyncMock(return_value=MagicMock(id=PydanticObjectId(wf_id)))
    mock_service.get_workflow_run = AsyncMock(side_effect=ValueError(f"Workflow run {run_id} not found"))

    mock_acl = MagicMock()
    mock_acl.check_user_permission = AsyncMock(return_value=None)

    user_context = {"user_id": str(PydanticObjectId()), "username": "u", "groups": [], "scopes": []}

    with pytest.raises(HTTPException) as exc_info:
        await list_node_runs(
            workflow_id=wf_id,
            run_id=run_id,
            user_context=user_context,
            workflow_service=mock_service,
            acl_service=mock_acl,
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_list_node_runs_run_wrong_workflow_returns_400():
    from registry.api.v1.workflow.workflow_routes import list_node_runs

    wf_id = str(PydanticObjectId())
    run_id = str(PydanticObjectId())

    mock_service = MagicMock()
    mock_service.get_workflow_by_id = AsyncMock(return_value=MagicMock(id=PydanticObjectId(wf_id)))
    mock_service.get_workflow_run = AsyncMock(
        side_effect=ValueError(f"Workflow run {run_id} does not belong to workflow {wf_id}")
    )

    mock_acl = MagicMock()
    mock_acl.check_user_permission = AsyncMock(return_value=None)

    user_context = {"user_id": str(PydanticObjectId()), "username": "u", "groups": [], "scopes": []}

    with pytest.raises(HTTPException) as exc_info:
        await list_node_runs(
            workflow_id=wf_id,
            run_id=run_id,
            user_context=user_context,
            workflow_service=mock_service,
            acl_service=mock_acl,
        )

    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# get_node_run: GET /workflows/{workflow_id}/runs/{run_id}/nodes/{node_run_id}
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_get_node_run_returns_detail():
    from types import SimpleNamespace

    from registry.api.v1.workflow.workflow_routes import get_node_run
    from registry.schemas.workflow_api_schemas import NodeRunOutput
    from registry_pkgs.models.enums import WorkflowRunStatus

    wf_id = str(PydanticObjectId())
    run_id = str(PydanticObjectId())
    nr_id = str(PydanticObjectId())

    fake_run = SimpleNamespace(id=PydanticObjectId(run_id))
    fake_nr = SimpleNamespace(
        id=PydanticObjectId(nr_id),
        node_id="n1",
        node_name="step-1",
        workflow_run_id=PydanticObjectId(run_id),
        status=WorkflowRunStatus.COMPLETED,
        attempt=1,
        input_snapshot={"a": 1},
        output_snapshot={"b": 2},
        error=None,
        started_at=None,
        finished_at=None,
    )

    mock_service = MagicMock()
    mock_service.get_workflow_by_id = AsyncMock(return_value=MagicMock(id=PydanticObjectId(wf_id)))
    mock_service.get_workflow_run = AsyncMock(return_value=(fake_run, []))
    mock_service.get_node_run = AsyncMock(return_value=fake_nr)

    mock_acl = MagicMock()
    mock_acl.check_user_permission = AsyncMock(return_value=None)

    user_context = {"user_id": str(PydanticObjectId()), "username": "u", "groups": [], "scopes": []}

    response = await get_node_run(
        workflow_id=wf_id,
        run_id=run_id,
        node_run_id=nr_id,
        user_context=user_context,
        workflow_service=mock_service,
        acl_service=mock_acl,
    )

    assert isinstance(response, NodeRunOutput)
    assert response.id == nr_id
    assert response.nodeId == "n1"
    assert response.inputSnapshot == {"a": 1}
    assert response.outputSnapshot == {"b": 2}
    mock_service.get_workflow_run.assert_awaited_once_with(workflow_id=wf_id, run_id=run_id)
    mock_service.get_node_run.assert_awaited_once_with(run_id, nr_id)


@pytest.mark.asyncio
async def test_get_node_run_forbidden_without_view():
    from registry.api.v1.workflow.workflow_routes import get_node_run

    mock_acl = MagicMock()
    mock_acl.check_user_permission = AsyncMock(side_effect=HTTPException(status_code=403, detail="Forbidden"))

    mock_service = MagicMock()
    mock_service.get_workflow_by_id = AsyncMock(return_value=MagicMock())

    user_context = {"user_id": str(PydanticObjectId()), "username": "u", "groups": [], "scopes": []}

    with pytest.raises(HTTPException) as exc_info:
        await get_node_run(
            workflow_id=str(PydanticObjectId()),
            run_id=str(PydanticObjectId()),
            node_run_id=str(PydanticObjectId()),
            user_context=user_context,
            workflow_service=mock_service,
            acl_service=mock_acl,
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_get_node_run_node_not_found_returns_404():
    from types import SimpleNamespace

    from registry.api.v1.workflow.workflow_routes import get_node_run

    wf_id = str(PydanticObjectId())
    run_id = str(PydanticObjectId())
    nr_id = str(PydanticObjectId())

    fake_run = SimpleNamespace(id=PydanticObjectId(run_id))

    mock_service = MagicMock()
    mock_service.get_workflow_by_id = AsyncMock(return_value=MagicMock(id=PydanticObjectId(wf_id)))
    mock_service.get_workflow_run = AsyncMock(return_value=(fake_run, []))
    mock_service.get_node_run = AsyncMock(side_effect=ValueError(f"NodeRun {nr_id!r} not found"))

    mock_acl = MagicMock()
    mock_acl.check_user_permission = AsyncMock(return_value=None)

    user_context = {"user_id": str(PydanticObjectId()), "username": "u", "groups": [], "scopes": []}

    with pytest.raises(HTTPException) as exc_info:
        await get_node_run(
            workflow_id=wf_id,
            run_id=run_id,
            node_run_id=nr_id,
            user_context=user_context,
            workflow_service=mock_service,
            acl_service=mock_acl,
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_get_node_run_node_wrong_run_returns_400():
    from types import SimpleNamespace

    from registry.api.v1.workflow.workflow_routes import get_node_run

    wf_id = str(PydanticObjectId())
    run_id = str(PydanticObjectId())
    nr_id = str(PydanticObjectId())

    fake_run = SimpleNamespace(id=PydanticObjectId(run_id))

    mock_service = MagicMock()
    mock_service.get_workflow_by_id = AsyncMock(return_value=MagicMock(id=PydanticObjectId(wf_id)))
    mock_service.get_workflow_run = AsyncMock(return_value=(fake_run, []))
    mock_service.get_node_run = AsyncMock(
        side_effect=ValueError(f"NodeRun {nr_id!r} does not belong to run {run_id!r}")
    )

    mock_acl = MagicMock()
    mock_acl.check_user_permission = AsyncMock(return_value=None)

    user_context = {"user_id": str(PydanticObjectId()), "username": "u", "groups": [], "scopes": []}

    with pytest.raises(HTTPException) as exc_info:
        await get_node_run(
            workflow_id=wf_id,
            run_id=run_id,
            node_run_id=nr_id,
            user_context=user_context,
            workflow_service=mock_service,
            acl_service=mock_acl,
        )

    assert exc_info.value.status_code == 400
