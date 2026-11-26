from collections.abc import Iterator
from unittest.mock import AsyncMock, Mock

import pytest
from fakeredis import FakeAsyncRedis, FakeRedis, FakeServer
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.exceptions import RedisError

from registry.api.v1.consent_management_routes import router as consent_router
from registry.api.v1.mcp.consent_routes import router as approval_router
from registry.app_factory import _configure_middleware, _customize_openapi
from registry.constants import MAX_CONSENT_CLIENT_ID_LENGTH, MAX_CONSENT_RESOURCE_PATH_LENGTH
from registry.container import RegistryContainer
from registry.core.config import settings
from registry.core.exception_handler import register_validation_exception_handler
from registry.routers import register_routers
from registry.services.a2a_agent_service import A2AAgentService
from registry.services.server_service import ServerServiceV1
from registry.utils.csrf import compute_csrf_token
from registry_pkgs.core.consent_store import CLIENT_CONSENT_FIELD
from registry_pkgs.core.jwt_tokens import mint_crud_session_token, mint_managed_agent_token

URL = "/api/v1/consents/clients"
USER_A = "507f1f77bcf86cd799439011"
USER_B = "507f1f77bcf86cd799439012"
CLIENT_ID = "mcp-client-shared"
BATCH_URL = f"{URL}/revoke"
CLIENT_URL = f"{URL}/{CLIENT_ID}"
RESOURCES_URL = f"{CLIENT_URL}/resources/revoke"
SERVER = {"type": "mcp_server", "path": "/shared"}
AGENT = {"type": "a2a_agent", "path": "/shared"}


@pytest.fixture
def mock_rbac_for_tests() -> None:
    """Keep the real scope checks for this endpoint instead of the suite-wide bypass."""


@pytest.fixture
def container() -> RegistryContainer:
    redis = FakeServer()
    container = RegistryContainer(
        settings,
        db_client=Mock(),
        redis_client=FakeAsyncRedis(server=redis, decode_responses=True),
        sync_redis_client=FakeRedis(server=redis, decode_responses=True),
    )
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
    register_validation_exception_handler(app)
    register_routers(app)
    with TestClient(app) as client:
        yield client


def _sign_in(client: TestClient, user_id: str, scope: str = "user-read") -> dict[str, str]:
    token = mint_crud_session_token(
        settings.jwt_token_config,
        subject=user_id,
        token_type="access_token",
        expires_in_seconds=300,
        extra_claims={"scope": scope},
    )
    client.cookies.set(settings.session_cookie_name, token)
    return {settings.csrf_header_name: compute_csrf_token(token)}


def test_list_is_scoped_to_session_and_preserves_expiry(client: TestClient, container: RegistryContainer) -> None:
    container.oauth_client_store.save_client(CLIENT_ID, {"client_name": "Shared app", "client_secret": "test-only"})
    grant_id = container.consent_store.grant_client_consent(USER_A, CLIENT_ID)
    container.consent_store.grant_server_consent(USER_A, CLIENT_ID, "/alice", require_app_consent=True)
    # A legacy resource-only consent is still visible after resource grants start requiring app consent.
    container.sync_redis_client.hset(
        f"{settings.auth_server_redis_key_prefix}:mcp:consent:{USER_B}:{CLIENT_ID}", "/bob", "1"
    )

    prefix = settings.auth_server_redis_key_prefix
    keys = [f"{prefix}:mcp:consent:{user}:{CLIENT_ID}" for user in (USER_A, USER_B)]
    keys.append(f"{prefix}:oauth:client:{CLIENT_ID}")
    for key in keys:
        assert container.sync_redis_client.expire(key, 300)
    expiries = [container.sync_redis_client.pexpiretime(key) for key in keys]

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

    assert [container.sync_redis_client.pexpiretime(key) for key in keys] == expiries


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


@pytest.mark.parametrize("subject", ["", "legacy-username"])
def test_session_without_valid_user_id_is_unauthorized(client: TestClient, subject: str) -> None:
    _sign_in(client, subject)
    response = client.get(URL)
    assert response.status_code == 401
    assert response.json() == {"detail": "Session authentication required"}


def test_cookie_without_user_read_is_forbidden(client: TestClient) -> None:
    _sign_in(client, USER_A, "servers-read")
    response = client.get(URL)
    assert response.status_code == 403
    assert response.json() == {"detail": "Insufficient permissions"}


def test_store_failure_is_not_an_empty_list(
    client: TestClient, container: RegistryContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sign_in(client, USER_A)
    monkeypatch.setattr(container.sync_redis_client, "scan", Mock(side_effect=RedisError("Redis unavailable")))
    response = client.get(URL)
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}


@pytest.mark.parametrize(
    ("method", "url", "body"),
    [
        ("DELETE", CLIENT_URL, None),
        ("POST", BATCH_URL, {"clientIds": [CLIENT_ID]}),
        ("POST", RESOURCES_URL, {"resources": [SERVER]}),
    ],
)
def test_writes_require_session_csrf_and_user_read(
    client: TestClient, method: str, url: str, body: dict | None
) -> None:
    bearer = mint_managed_agent_token(
        settings.jwt_token_config,
        subject=USER_A,
        client_id=CLIENT_ID,
        requested_scopes="mcp-proxy-ops",
        expires_in_seconds=300,
    )
    assert client.request(method, url, json=body, headers={"Authorization": f"Bearer {bearer}"}).status_code == 401

    _sign_in(client, USER_A)
    missing_csrf = client.request(method, url, json=body)
    assert missing_csrf.status_code == 403
    assert missing_csrf.json() == {"detail": "CSRF token missing"}
    invalid_csrf = client.request(method, url, json=body, headers={settings.csrf_header_name: "invalid"})
    assert invalid_csrf.status_code == 403
    assert invalid_csrf.json() == {"detail": "CSRF token invalid"}

    headers = _sign_in(client, USER_A, scope="servers-read")
    insufficient_scope = client.request(method, url, json=body, headers=headers)
    assert insufficient_scope.status_code == 403
    assert insufficient_scope.json() == {"detail": "Insufficient permissions"}


@pytest.mark.parametrize("selection", ["single", "batch", "all"])
def test_revoke_app_only_clears_callers_consent_and_client_info(
    client: TestClient, container: RegistryContainer, selection: str
) -> None:
    store = container.consent_store
    redis = container.sync_redis_client
    cache_a = f"{settings.redis_key_prefix}:mcpgw:client:{USER_A}:{CLIENT_ID}"
    cache_b = f"{settings.redis_key_prefix}:mcpgw:client:{USER_B}:{CLIENT_ID}"
    for key in (cache_a, cache_b, f"{cache_a}:device", f"{cache_b}:device"):
        redis.set(key, "client info")
    container.oauth_client_store.save_client(CLIENT_ID, {"client_name": "Shared registration"})
    for user in (USER_A, USER_B):
        store.grant_client_consent(user, CLIENT_ID)
        store.grant_server_consent(user, CLIENT_ID, "/shared", require_app_consent=True)

    headers = _sign_in(client, USER_A)
    if selection == "single":
        for _ in range(2):
            response = client.delete(CLIENT_URL, headers=headers, params={"user_id": USER_B})
            assert response.status_code == 204
            assert response.content == b""
    else:
        body = {"all": True} if selection == "all" else {"clientIds": [CLIENT_ID]}
        response = client.post(BATCH_URL, headers=headers, json=body, params={"user_id": USER_B})
        assert response.status_code == 200
        assert response.json() == {"results": [{"clientId": CLIENT_ID, "status": "revoked"}]}
    assert not store.get_entries(USER_A, [CLIENT_ID])
    assert store.get_client_grant_id(USER_B, CLIENT_ID)
    assert store.has_server_consent(USER_B, CLIENT_ID, "/shared")
    assert redis.exists(cache_a, f"{cache_a}:device") == 0
    assert redis.exists(cache_b, f"{cache_b}:device") == 2
    assert container.oauth_client_store.get_client(CLIENT_ID)["client_name"] == "Shared registration"


def test_resource_revoke_preserves_other_fields_and_caches(client: TestClient, container: RegistryContainer) -> None:
    store = container.consent_store
    keys = [f"{settings.redis_key_prefix}:mcpgw:client:{user}:{CLIENT_ID}" for user in (USER_A, USER_B)]
    for user in (USER_A, USER_B):
        store.grant_client_consent(user, CLIENT_ID)
        store.grant_server_consent(user, CLIENT_ID, "/shared", require_app_consent=True)
        store.grant_agent_consent(user, CLIENT_ID, "/shared", require_app_consent=True)
    for key in keys:
        container.sync_redis_client.set(key, "client info")

    missing = {"type": "a2a_agent", "path": "/missing"}
    response = client.post(
        RESOURCES_URL,
        headers=_sign_in(client, USER_A),
        params={"user_id": USER_B},
        json={"resources": [SERVER, missing, SERVER]},
    )
    assert response.status_code == 200
    assert response.json() == {
        "results": [
            {**SERVER, "status": "revoked"},
            {**missing, "status": "not_found"},
            {**SERVER, "status": "revoked"},
        ]
    }
    assert not store.has_server_consent(USER_A, CLIENT_ID, "/shared")
    assert store.has_server_consent(USER_B, CLIENT_ID, "/shared")
    for user in (USER_A, USER_B):
        assert store.get_client_grant_id(user, CLIENT_ID)
        assert store.has_agent_consent(user, CLIENT_ID, "/shared")
    assert container.sync_redis_client.exists(*keys) == len(keys)


def test_protected_clients_are_rejected_without_deleting_consent(
    client: TestClient, container: RegistryContainer
) -> None:
    protected = [settings.registry_app_name, settings.headless_agent_client_id]
    headers = _sign_in(client, USER_A)
    for client_id in [CLIENT_ID, *protected]:
        container.consent_store.grant_client_consent(USER_A, client_id)

    response = client.post(BATCH_URL, headers=headers, json={"clientIds": [CLIENT_ID, *protected, "missing"]})
    assert response.status_code == 200
    assert response.json() == {
        "results": [
            {"clientId": CLIENT_ID, "status": "revoked"},
            *({"clientId": cid, "status": "rejected"} for cid in protected),
            {"clientId": "missing", "status": "not_found"},
        ]
    }
    for client_id in protected:
        delete_response = client.delete(f"{URL}/{client_id}", headers=headers)
        assert delete_response.status_code == 400
        response = client.post(f"{URL}/{client_id}/resources/revoke", headers=headers, json={"resources": [SERVER]})
        assert response.status_code == 400
        assert container.consent_store.get_client_grant_id(USER_A, client_id)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"clientIds": []},
        {"clientIds": [CLIENT_ID] * 101},
        {"all": True, "clientIds": [CLIENT_ID]},
        {"all": False},
        {"all": 1},
        {"all": True, "clientId": CLIENT_ID},
        {"clientIds": [CLIENT_ID, ""]},
        {"clientIds": [CLIENT_ID, "c" * (MAX_CONSENT_CLIENT_ID_LENGTH + 1)]},
    ],
)
def test_invalid_client_selection_is_rejected_before_deletion(
    client: TestClient, container: RegistryContainer, body: dict
) -> None:
    container.consent_store.grant_client_consent(USER_A, CLIENT_ID)
    response = client.post(BATCH_URL, headers=_sign_in(client, USER_A), json=body)
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)
    assert container.consent_store.get_client_grant_id(USER_A, CLIENT_ID)


@pytest.mark.parametrize(
    "body",
    [
        {"resources": []},
        {"resources": [AGENT] * 101},
        {"resources": [{"type": "app", "path": "/"}]},
        {"resources": [{"type": "unknown", "path": "/"}]},
        {"resources": [{"type": "mcp_server", "path": ""}]},
        {"resources": [{"type": "mcp_server", "path": "/" + "p" * MAX_CONSENT_RESOURCE_PATH_LENGTH}]},
        {"resources": [{**SERVER, "kind": "mcp_server"}]},
        {"resources": [SERVER], "all": True},
    ],
)
def test_invalid_resource_selection_returns_422(client: TestClient, container: RegistryContainer, body: dict) -> None:
    container.consent_store.grant_client_consent(USER_A, CLIENT_ID)
    container.consent_store.grant_server_consent(USER_A, CLIENT_ID, SERVER["path"], require_app_consent=True)
    response = client.post(RESOURCES_URL, headers=_sign_in(client, USER_A), json=body)
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)
    assert container.consent_store.has_server_consent(USER_A, CLIENT_ID, SERVER["path"])


def test_revoke_accepts_strings_at_the_length_limits(client: TestClient) -> None:
    headers = _sign_in(client, USER_A)
    client_id = "c" * MAX_CONSENT_CLIENT_ID_LENGTH
    response = client.post(BATCH_URL, headers=headers, json={"clientIds": [client_id]})
    assert response.status_code == 200
    assert response.json() == {"results": [{"clientId": client_id, "status": "not_found"}]}

    path = "/" + "p" * (MAX_CONSENT_RESOURCE_PATH_LENGTH - 1)
    response = client.post(RESOURCES_URL, headers=headers, json={"resources": [{"type": "mcp_server", "path": path}]})
    assert response.status_code == 200
    assert response.json() == {"results": [{"type": "mcp_server", "path": path, "status": "not_found"}]}


def test_reserved_resource_field_returns_400_before_any_deletion(
    client: TestClient, container: RegistryContainer
) -> None:
    store = container.consent_store
    grant = store.grant_client_consent(USER_A, CLIENT_ID)
    store.grant_server_consent(USER_A, CLIENT_ID, "/shared", require_app_consent=True)
    response = client.post(
        RESOURCES_URL,
        headers=_sign_in(client, USER_A),
        json={"resources": [SERVER, {"type": "mcp_server", "path": CLIENT_CONSENT_FIELD}]},
    )
    assert response.status_code == 400
    assert store.get_client_grant_id(USER_A, CLIENT_ID) == grant
    assert store.has_server_consent(USER_A, CLIENT_ID, "/shared")


def test_redis_errors_are_not_reported_as_success(
    client: TestClient, container: RegistryContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _sign_in(client, USER_A)
    monkeypatch.setattr(container.sync_redis_client, "pipeline", Mock(side_effect=RedisError("Redis unavailable")))
    for response in (
        client.delete(CLIENT_URL, headers=headers),
        client.post(BATCH_URL, headers=headers, json={"clientIds": [CLIENT_ID]}),
        client.post(RESOURCES_URL, headers=headers, json={"resources": [AGENT]}),
    ):
        assert response.status_code == 500
        assert response.json() == {"detail": "Internal server error"}


@pytest.mark.parametrize(
    ("method", "path", "operation_errors"),
    [
        ("delete", f"{URL}/{{client_id}}", ("400",)),
        ("post", BATCH_URL, ("400",)),
        ("post", f"{URL}/{{client_id}}/resources/revoke", ("400",)),
        ("post", "/api/v1/mcp/consent/server", ("404", "409")),
        ("post", "/api/v1/mcp/consent/agent", ("404", "409")),
    ],
)
def test_openapi_describes_write_auth_and_actual_error_shape(
    method: str, path: str, operation_errors: tuple[str, ...]
) -> None:
    app = FastAPI()
    app.include_router(consent_router, prefix="/api/v1")
    app.include_router(approval_router, prefix="/api/v1")
    schema = _customize_openapi(app)()
    operation = schema["paths"][path][method]
    assert operation["security"] == [{"SessionCookie": []}]
    assert any(
        p["name"] == settings.csrf_header_name and p["in"] == "header" and p["required"]
        for p in operation["parameters"]
    )
    responses = operation["responses"]
    for code in ("401", "403", "422", "500", *operation_errors):
        assert responses[code]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ConsentErrorResponse"
        }
    assert schema["components"]["schemas"]["ConsentErrorResponse"]["properties"]["detail"]["type"] == "string"
    if "409" in operation_errors:
        assert responses["409"]["description"] == (
            "This app's access was revoked. Reconnect it from your AI app, then try again."
        )
    if method == "delete":
        assert "content" not in responses["204"]
