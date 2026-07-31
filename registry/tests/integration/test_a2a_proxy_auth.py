"""Integration coverage for A2A direct-connect authentication policy."""

from collections.abc import Generator
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from registry.api.proxy_routes import router
from registry.core.config import settings
from registry.deps import get_a2a_agent_service, get_a2a_client_registry, get_acl_service
from registry.middleware.auth import UnifiedAuthMiddleware
from registry_pkgs.core.jwt_tokens import mint_managed_agent_token

pytestmark = [pytest.mark.integration, pytest.mark.auth, pytest.mark.proxy]

_DCR_CLIENT_ID = "mcp-client-integration-test"
_USER_ID = "507f1f77bcf86cd799439011"


def _mint_dcr_managed_agent_token() -> str:
    return mint_managed_agent_token(
        settings.jwt_token_config,
        subject="integration-user",
        client_id=_DCR_CLIENT_ID,
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
        headers={"Authorization": f"Bearer {_mint_dcr_managed_agent_token()}"},
    )

    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32001
    a2a_proxy_context.agent_service.get_agent_by_path.assert_not_awaited()


def test_valid_dcr_jwt_is_denied_by_http_json_a2a_route(a2a_proxy_context: SimpleNamespace) -> None:
    response = a2a_proxy_context.client.get(
        "/proxy/a2a/test-agent/tasks/1",
        headers={"Authorization": f"Bearer {_mint_dcr_managed_agent_token()}"},
    )

    assert response.status_code == 403
    assert response.json() == {
        "error": "Direct-connect A2A invocation requires a token generated from the Jarvis Registry frontend."
    }
    a2a_proxy_context.agent_service.get_agent_by_path.assert_not_awaited()
