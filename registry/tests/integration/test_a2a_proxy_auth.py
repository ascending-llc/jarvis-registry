"""Integration coverage for A2A direct-connect authentication policy."""

from collections.abc import Generator
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from registry.api.proxy_routes import _A2A_ACCESS_DENIED_ERROR_CODE, router
from registry.core.config import settings
from registry.deps import get_a2a_agent_service, get_a2a_client_registry, get_acl_service
from registry.middleware.auth import UnifiedAuthMiddleware
from registry.services.generated_token_policy import INTERACTIVE_CLIENT_ID
from registry_pkgs.core.jwt_tokens import mint_managed_agent_token

pytestmark = [pytest.mark.integration, pytest.mark.auth, pytest.mark.proxy]

_DCR_CLIENT_ID = "mcp-client-integration-test"
_USER_ID = "507f1f77bcf86cd799439011"


def _mint_managed_agent_token(client_id: str) -> str:
    return mint_managed_agent_token(
        settings.jwt_token_config,
        subject="integration-user",
        client_id=client_id,
        expires_in_seconds=3600,
        extra_claims={
            "user_id": _USER_ID,
            "scope": "a2a-proxy-ops",
        },
    )


@pytest.fixture
def a2a_proxy_context() -> Generator[SimpleNamespace, None, None]:
    app = FastAPI()
    app.add_middleware(UnifiedAuthMiddleware)
    app.include_router(router, prefix="/proxy")

    agent_service = SimpleNamespace(get_agent_by_path=AsyncMock())
    app.dependency_overrides[get_a2a_agent_service] = lambda: agent_service
    app.dependency_overrides[get_acl_service] = lambda: AsyncMock()
    app.dependency_overrides[get_a2a_client_registry] = lambda: AsyncMock()

    with TestClient(app) as client:
        yield SimpleNamespace(client=client, agent_service=agent_service)


def test_valid_dcr_jwt_is_denied_by_jsonrpc_a2a_route(a2a_proxy_context: SimpleNamespace) -> None:
    response = a2a_proxy_context.client.post(
        "/proxy/a2a/test-agent",
        headers={"Authorization": f"Bearer {_mint_managed_agent_token(_DCR_CLIENT_ID)}"},
    )

    assert response.status_code == 200
    assert response.json()["error"]["code"] == _A2A_ACCESS_DENIED_ERROR_CODE
    a2a_proxy_context.agent_service.get_agent_by_path.assert_not_awaited()


def test_valid_dcr_jwt_is_denied_by_http_json_a2a_route(a2a_proxy_context: SimpleNamespace) -> None:
    response = a2a_proxy_context.client.get(
        "/proxy/a2a/test-agent/tasks/1",
        headers={"Authorization": f"Bearer {_mint_managed_agent_token(_DCR_CLIENT_ID)}"},
    )

    assert response.status_code == 403
    assert response.json() == {
        "error": "Direct-connect A2A invocation requires a token generated from the Jarvis Registry frontend."
    }
    a2a_proxy_context.agent_service.get_agent_by_path.assert_not_awaited()


@pytest.mark.parametrize("client_id", [INTERACTIVE_CLIENT_ID, settings.headless_agent_client_id])
def test_allowed_client_reaches_agent_lookup_via_jsonrpc_a2a_route(
    a2a_proxy_context: SimpleNamespace, client_id: str
) -> None:
    """An allowlisted client_id must pass the gate through the real routing/DI stack.

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
    """An allowlisted client_id must pass the gate through the real routing/DI stack.

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


def test_http_json_proxy_route_is_registered_as_api_route() -> None:
    """Pins the exact defect class that broke this route: a plain Starlette `Route`

    (from `@router.route`) bypasses FastAPI's dependency-injection layer entirely, so a
    future revert of the `@router.api_route` decorator would silently break every real
    request to this endpoint without any Python-level error at import time.
    """
    route = next(r for r in router.routes if getattr(r, "path", None) == "/a2a/{agent_path}/{http_json_path:path}")
    assert isinstance(route, APIRoute)
