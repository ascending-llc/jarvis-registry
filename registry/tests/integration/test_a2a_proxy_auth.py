"""Integration coverage for A2A direct-connect authentication policy."""

from collections.abc import Generator
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from beanie import PydanticObjectId
from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from registry.api.proxy_routes import _A2A_ACCESS_DENIED_ERROR_CODE, router
from registry.core.config import settings
from registry.deps import get_a2a_agent_service, get_a2a_client_registry, get_acl_service
from registry.middleware.auth import UnifiedAuthMiddleware
from registry.middleware.rbac import ScopePermissionMiddleware
from registry.services.generated_token_policy import INTERACTIVE_CLIENT_ID
from registry_pkgs.core.jwt_tokens import mint_managed_agent_token
from registry_pkgs.models.enums import AgentCoreRuntimeAccessMode

pytestmark = [pytest.mark.integration, pytest.mark.auth, pytest.mark.proxy]

_DCR_CLIENT_ID = "a2a-client-integration-test"
_USER_ID = "507f1f77bcf86cd799439011"
_ORIGINAL_HAS_PERMISSION = ScopePermissionMiddleware._has_permission


def _mint_managed_agent_token(client_id: str, scope: str = "a2a-proxy-ops") -> str:
    return mint_managed_agent_token(
        settings.jwt_token_config,
        subject="integration-user",
        client_id=client_id,
        requested_scopes=scope,
        expires_in_seconds=3600,
        extra_claims={
            "user_id": _USER_ID,
        },
    )


def _enabled_agent(*, iam: bool = False) -> SimpleNamespace:
    runtime_access = SimpleNamespace(mode=AgentCoreRuntimeAccessMode.IAM) if iam else None
    return SimpleNamespace(
        id=PydanticObjectId(),
        path="test-agent",
        config=SimpleNamespace(enabled=True, runtimeAccess=runtime_access, url=None),
        card=SimpleNamespace(url="https://agent.example.com/a2a"),
        federationMetadata=None,
    )


@pytest.fixture
def a2a_proxy_context(monkeypatch: pytest.MonkeyPatch) -> Generator[SimpleNamespace, None, None]:
    # The global test fixture bypasses RBAC by default. This suite verifies the real
    # scope boundary, so restore the production implementation before building the app.
    monkeypatch.setattr(ScopePermissionMiddleware, "_has_permission", _ORIGINAL_HAS_PERMISSION)

    app = FastAPI()
    app.add_middleware(ScopePermissionMiddleware)
    app.add_middleware(UnifiedAuthMiddleware)
    app.include_router(router, prefix="/proxy")

    agent_service = SimpleNamespace(
        get_agent_by_path=AsyncMock(),
        build_managed_agent_card=Mock(),
    )
    acl_service = AsyncMock()
    client_registry = AsyncMock()
    app.dependency_overrides[get_a2a_agent_service] = lambda: agent_service
    app.dependency_overrides[get_acl_service] = lambda: acl_service
    app.dependency_overrides[get_a2a_client_registry] = lambda: client_registry

    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            agent_service=agent_service,
            acl_service=acl_service,
            client_registry=client_registry,
        )


def test_dcr_jwt_with_a2a_scope_reaches_jsonrpc_agent_lookup(a2a_proxy_context: SimpleNamespace) -> None:
    a2a_proxy_context.agent_service.get_agent_by_path.return_value = None

    response = a2a_proxy_context.client.post(
        "/proxy/a2a/test-agent",
        headers={"Authorization": f"Bearer {_mint_managed_agent_token(_DCR_CLIENT_ID)}"},
    )

    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32603
    a2a_proxy_context.agent_service.get_agent_by_path.assert_awaited_once_with("test-agent")


def test_dcr_jwt_with_a2a_scope_reaches_http_json_agent_lookup(a2a_proxy_context: SimpleNamespace) -> None:
    a2a_proxy_context.agent_service.get_agent_by_path.return_value = None

    response = a2a_proxy_context.client.get(
        "/proxy/a2a/test-agent/tasks/1",
        headers={"Authorization": f"Bearer {_mint_managed_agent_token(_DCR_CLIENT_ID)}"},
    )

    assert response.status_code == 404
    assert response.json() == {"error": "A2A agent with path 'test-agent' not found"}
    a2a_proxy_context.agent_service.get_agent_by_path.assert_awaited_once_with("test-agent")


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/proxy/a2a/test-agent"),
        ("GET", "/proxy/a2a/test-agent/tasks/1"),
    ],
)
def test_dcr_jwt_without_a2a_scope_is_rejected_before_agent_lookup(
    a2a_proxy_context: SimpleNamespace,
    method: str,
    path: str,
) -> None:
    response = a2a_proxy_context.client.request(
        method,
        path,
        headers={"Authorization": f"Bearer {_mint_managed_agent_token(_DCR_CLIENT_ID, scope='agents-read')}"},
    )

    assert response.status_code == 401
    a2a_proxy_context.agent_service.get_agent_by_path.assert_not_awaited()


def test_managed_agent_card_routes_are_public_and_not_swallowed(
    a2a_proxy_context: SimpleNamespace,
) -> None:
    agent = SimpleNamespace(config=SimpleNamespace(enabled=True))
    managed_card = {
        "name": "Managed Agent",
        "preferredTransport": "JSONRPC",
        "url": "https://registry.example/proxy/a2a/test-agent",
    }
    a2a_proxy_context.agent_service.get_agent_by_path.return_value = agent
    a2a_proxy_context.agent_service.build_managed_agent_card.return_value = managed_card

    primary = a2a_proxy_context.client.get("/proxy/a2a/test-agent/agent-card.json")
    alias = a2a_proxy_context.client.get("/proxy/a2a/test-agent/.well-known/agent-card.json")

    assert primary.status_code == 200
    assert primary.content == alias.content
    assert primary.json() == managed_card
    assert a2a_proxy_context.agent_service.get_agent_by_path.await_count == 2


@pytest.mark.parametrize("client_id", [INTERACTIVE_CLIENT_ID, settings.headless_agent_client_id])
def test_allowed_client_reaches_agent_lookup_via_jsonrpc_a2a_route(
    a2a_proxy_context: SimpleNamespace, client_id: str
) -> None:
    """A first-party token with the required scope passes the real routing/DI stack.

    This is the regression guard for the `@router.route` -> `@router.api_route` fix: under
    the old decorator, FastAPI's dependency injection never ran, so `user_context`, `agent_path`,
    and the `Depends(...)` services were never bound and the request 500'd before reaching here.
    """
    a2a_proxy_context.agent_service.get_agent_by_path.return_value = None

    response = a2a_proxy_context.client.post(
        "/proxy/a2a/test-agent",
        headers={"Authorization": f"Bearer {_mint_managed_agent_token(client_id)}"},
    )

    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32603
    a2a_proxy_context.agent_service.get_agent_by_path.assert_awaited_once_with("test-agent")


@pytest.mark.parametrize("client_id", [INTERACTIVE_CLIENT_ID, settings.headless_agent_client_id])
def test_allowed_client_reaches_agent_lookup_via_http_json_a2a_route(
    a2a_proxy_context: SimpleNamespace, client_id: str
) -> None:
    """A first-party token with the required scope passes the real routing/DI stack.

    This is the regression guard for the `@router.route` -> `@router.api_route` fix: under
    the old decorator, FastAPI's dependency injection never ran, so `user_context`, `agent_path`,
    `http_json_path`, and the `Depends(...)` services were never bound and the request 500'd
    before reaching here.
    """
    a2a_proxy_context.agent_service.get_agent_by_path.return_value = None

    response = a2a_proxy_context.client.get(
        "/proxy/a2a/test-agent/tasks/1",
        headers={"Authorization": f"Bearer {_mint_managed_agent_token(client_id)}"},
    )

    assert response.status_code == 404
    assert response.json() == {"error": "A2A agent with path 'test-agent' not found"}
    a2a_proxy_context.agent_service.get_agent_by_path.assert_awaited_once_with("test-agent")


def test_dcr_jsonrpc_token_still_enforces_agent_acl(a2a_proxy_context: SimpleNamespace) -> None:
    a2a_proxy_context.agent_service.get_agent_by_path.return_value = _enabled_agent()
    a2a_proxy_context.acl_service.check_user_permission.side_effect = HTTPException(status_code=403)

    response = a2a_proxy_context.client.post(
        "/proxy/a2a/test-agent",
        headers={"Authorization": f"Bearer {_mint_managed_agent_token(_DCR_CLIENT_ID)}"},
    )

    assert response.status_code == 200
    assert response.json()["error"]["code"] == _A2A_ACCESS_DENIED_ERROR_CODE
    assert response.json()["error"]["message"] == "Access denied to A2A agent 'test-agent'"
    a2a_proxy_context.client_registry.get_client.assert_not_awaited()


def test_dcr_http_json_token_still_enforces_agent_acl(a2a_proxy_context: SimpleNamespace) -> None:
    a2a_proxy_context.agent_service.get_agent_by_path.return_value = _enabled_agent()
    a2a_proxy_context.acl_service.check_user_permission.side_effect = HTTPException(status_code=403)

    response = a2a_proxy_context.client.get(
        "/proxy/a2a/test-agent/tasks/1",
        headers={"Authorization": f"Bearer {_mint_managed_agent_token(_DCR_CLIENT_ID)}"},
    )

    assert response.status_code == 403
    assert response.json() == {"error": "Access denied to A2A agent 'test-agent'"}
    a2a_proxy_context.client_registry.get_client.assert_not_awaited()


def test_dcr_jsonrpc_token_receives_explicit_iam_error(a2a_proxy_context: SimpleNamespace) -> None:
    a2a_proxy_context.agent_service.get_agent_by_path.return_value = _enabled_agent(iam=True)

    response = a2a_proxy_context.client.post(
        "/proxy/a2a/test-agent",
        headers={"Authorization": f"Bearer {_mint_managed_agent_token(_DCR_CLIENT_ID)}"},
    )

    assert response.status_code == 200
    assert response.json()["error"] == {
        "code": -32004,
        "message": "A2A agent 'test-agent' uses IAM inbound auth which is not supported by this gateway",
    }
    a2a_proxy_context.client_registry.get_client.assert_not_awaited()


def test_dcr_http_json_token_receives_explicit_iam_error(a2a_proxy_context: SimpleNamespace) -> None:
    a2a_proxy_context.agent_service.get_agent_by_path.return_value = _enabled_agent(iam=True)

    response = a2a_proxy_context.client.get(
        "/proxy/a2a/test-agent/tasks/1",
        headers={"Authorization": f"Bearer {_mint_managed_agent_token(_DCR_CLIENT_ID)}"},
    )

    assert response.status_code == 501
    assert response.json() == {"error": "A2A agent 'test-agent' uses IAM inbound auth which is not supported"}
    a2a_proxy_context.client_registry.get_client.assert_not_awaited()


def test_http_json_proxy_route_is_registered_as_api_route() -> None:
    """Pins the exact defect class that broke this route: a plain Starlette `Route`

    (from `@router.route`) bypasses FastAPI's dependency-injection layer entirely, so a
    future revert of the `@router.api_route` decorator would silently break every real
    request to this endpoint without any Python-level error at import time.
    """
    route = next(r for r in router.routes if getattr(r, "path", None) == "/a2a/{agent_path}/{http_json_path:path}")
    assert isinstance(route, APIRoute)
