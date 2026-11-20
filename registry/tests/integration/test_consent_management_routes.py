from collections.abc import Iterator
from unittest.mock import AsyncMock, Mock

import pytest
from fakeredis import FakeRedis
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.exceptions import RedisError

from registry.app_factory import _configure_middleware
from registry.container import RegistryContainer
from registry.core.config import settings
from registry.routers import register_routers
from registry.services.a2a_agent_service import A2AAgentService
from registry.services.server_service import ServerServiceV1
from registry_pkgs.core.jwt_tokens import mint_crud_session_token, mint_managed_agent_token

URL = "/api/v1/consents/clients"
USER_A = "507f1f77bcf86cd799439011"
USER_B = "507f1f77bcf86cd799439012"
CLIENT_ID = "mcp-client-shared"


@pytest.fixture
def mock_rbac_for_tests() -> None:
    """Keep the real scope checks for this endpoint instead of the suite-wide bypass."""


@pytest.fixture
def container() -> RegistryContainer:
    container = RegistryContainer(settings, db_client=Mock(), redis_client=FakeRedis(decode_responses=True))
    container.server_service = AsyncMock(spec=ServerServiceV1)
    container.server_service.get_names_by_paths.return_value = {}
    container.a2a_agent_service = AsyncMock(spec=A2AAgentService)
    container.a2a_agent_service.get_names_by_paths.return_value = {}
    return container


@pytest.fixture
def client(container: RegistryContainer) -> Iterator[TestClient]:
    app = FastAPI()
    app.state.container = container
    _configure_middleware(app)
    register_routers(app)
    with TestClient(app) as client:
        yield client


def _sign_in(client: TestClient, user_id: str, scope: str = "user-read") -> None:
    token = mint_crud_session_token(
        settings.jwt_token_config,
        subject=user_id,
        token_type="access_token",
        expires_in_seconds=300,
        extra_claims={"scope": scope},
    )
    client.cookies.set(settings.session_cookie_name, token)


def test_list_is_scoped_to_session_and_preserves_expiry(client: TestClient, container: RegistryContainer) -> None:
    container.oauth_client_store.save_client(CLIENT_ID, {"client_name": "Shared app", "client_secret": "test-only"})
    grant_id = container.consent_store.grant_client_consent(USER_A, CLIENT_ID)
    container.consent_store.grant_server_consent(USER_A, CLIENT_ID, "/alice")
    container.consent_store.grant_server_consent(USER_B, CLIENT_ID, "/bob")

    prefix = settings.auth_server_redis_key_prefix
    keys = [f"{prefix}:mcp:consent:{user}:{CLIENT_ID}" for user in (USER_A, USER_B)]
    keys.append(f"{prefix}:oauth:client:{CLIENT_ID}")
    for key in keys:
        assert container.redis_client.expire(key, 300)
    expiries = [container.redis_client.pexpiretime(key) for key in keys]

    for user, other_user, path in ((USER_A, USER_B, "/alice"), (USER_B, USER_A, "/bob")):
        _sign_in(client, user)
        response = client.get(URL, params={"user_id": other_user})

        assert response.status_code == 200
        (row,) = response.json()["clients"]
        assert row["clientId"] == CLIENT_ID
        assert row["displayName"] == "Shared app"
        assert row["appApproved"] is (user == USER_A)
        assert row["resources"] == [{"type": "mcp_server", "path": path, "name": None}]
        assert grant_id not in response.text
        assert "client_secret" not in response.text

    assert [container.redis_client.pexpiretime(key) for key in keys] == expiries


def test_empty_list(client: TestClient) -> None:
    _sign_in(client, USER_A)
    response = client.get(URL)
    assert response.status_code == 200
    assert response.json() == {"clients": []}


def test_gateway_bearer_without_cookie_is_rejected(client: TestClient) -> None:
    token = mint_managed_agent_token(
        settings.jwt_token_config,
        subject=USER_A,
        client_id=CLIENT_ID,
        requested_scopes="mcp-proxy-ops",
        expires_in_seconds=300,
    )
    response = client.get(URL, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_cookie_without_user_read_is_forbidden(client: TestClient) -> None:
    _sign_in(client, USER_A, "servers-read")
    response = client.get(URL)
    assert response.status_code == 403
    assert response.json() == {"detail": "Insufficient permissions"}


def test_store_failure_is_not_an_empty_list(
    client: TestClient, container: RegistryContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sign_in(client, USER_A)
    monkeypatch.setattr(container.redis_client, "scan", Mock(side_effect=RedisError("Redis unavailable")))
    response = client.get(URL)
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
