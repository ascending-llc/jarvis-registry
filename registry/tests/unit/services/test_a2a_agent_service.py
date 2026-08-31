from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from a2a.client import A2AClientHTTPError
from a2a.types import AgentCard, TransportProtocol
from beanie import PydanticObjectId

from registry.schemas.a2a_agent_api_schemas import AgentCreateRequest, AgentUpdateRequest
from registry.services.a2a_agent_service import A2AAgentService, _normalize_config_url
from registry_pkgs.models.a2a_agent import A2AAgent, AgentConfig, NoSupportedTransportError
from registry_pkgs.testing.federation_metadata import (
    make_agentcore_a2a_metadata,
    make_azure_foundry_metadata,
)

_SENTINEL_SESSION = object()


class _AsyncCM:
    """Minimal async context manager wrapping a stand-in httpx client."""

    def __init__(self, client: object):
        self._client = client

    async def __aenter__(self) -> object:
        return self._client

    async def __aexit__(self, *_: object) -> bool:
        return False


def _service() -> A2AAgentService:
    # No repo -> the _schedule_* helpers return early (no asyncio.create_task).
    return A2AAgentService(a2a_agent_repo=None, azure_client_cache=MagicMock())


def _managed_card_agent(
    *,
    config: AgentConfig | None = None,
    grpc_only: bool = False,
) -> A2AAgent:
    additional_interfaces = [{"transport": "GRPC", "url": "https://origin.example/grpc"}]
    if not grpc_only:
        additional_interfaces = [
            {"transport": "HTTP+JSON", "url": "https://origin.example/http"},
            *additional_interfaces,
            {"transport": "JSONRPC", "url": "https://origin.example/jsonrpc"},
        ]

    card = AgentCard.model_validate(
        {
            "name": "Origin Agent",
            "description": "Origin description",
            "url": "https://origin.example/grpc",
            "version": "1.2.3",
            "protocolVersion": "0.3.0",
            "capabilities": {"streaming": True},
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["application/json"],
            "preferredTransport": "GRPC",
            "additionalInterfaces": additional_interfaces,
            "supportsAuthenticatedExtendedCard": True,
            "securitySchemes": {
                "originKey": {
                    "type": "apiKey",
                    "name": "X-Origin-Key",
                    "in": "header",
                }
            },
            "security": [{"originKey": []}],
            "signatures": [{"protected": "header", "signature": "signature"}],
            "skills": [
                {
                    "id": "weather",
                    "name": "Weather",
                    "description": "Returns weather",
                    "tags": ["weather"],
                    "security": [{"originKey": []}],
                }
            ],
            "provider": {
                "organization": "Origin Corp",
                "url": "https://origin.example",
            },
            "documentationUrl": "https://origin.example/docs",
            "iconUrl": "https://origin.example/icon.png",
        }
    )
    return A2AAgent.model_construct(
        id=PydanticObjectId(),
        path="weather-agent",
        card=card,
        config=config,
        author=PydanticObjectId(),
    )


def test_build_managed_agent_card_applies_all_transformations(monkeypatch: pytest.MonkeyPatch) -> None:
    # `registry_url` and `jwt_issuer` are deliberately set to different hosts here: `url` /
    # `additionalInterfaces[*].url` live on the registry backend (registry_url), while
    # `oauth2MetadataUrl` lives on the auth-server (jwt_issuer). A regression that conflates
    # the two (e.g. building the proxy URL from jwt_issuer) must fail this test.
    monkeypatch.setattr("registry.services.a2a_agent_service.settings.registry_url", "https://registry.example/gateway")
    monkeypatch.setattr("registry.services.a2a_agent_service.settings.jwt_issuer", "https://auth-issuer.example")
    monkeypatch.setattr(
        "registry.services.a2a_agent_service.settings.auth_server_external_url",
        "https://auth.example",
    )
    monkeypatch.setattr("registry.services.a2a_agent_service.settings.auth_provider", "entra")
    agent = _managed_card_agent(
        config=AgentConfig(
            title="Curated Agent",
            description="Curated description",
            type="jsonrpc",
            enabled=True,
        )
    )
    original_card = agent.card.model_dump(mode="json", by_alias=True, exclude_none=True)

    managed_card = _service().build_managed_agent_card(agent)

    proxy_url = "https://registry.example/gateway/proxy/a2a/weather-agent"
    assert managed_card["preferredTransport"] == TransportProtocol.jsonrpc
    assert managed_card["url"] == proxy_url
    assert managed_card["additionalInterfaces"] == [
        {"transport": "HTTP+JSON", "url": proxy_url},
        {"transport": "JSONRPC", "url": proxy_url},
    ]
    assert managed_card["supportsAuthenticatedExtendedCard"] is False
    assert managed_card["securitySchemes"] == {
        "oauth2": {
            "type": "oauth2",
            "flows": {
                "authorizationCode": {
                    "authorizationUrl": "https://auth.example/oauth2/login/entra",
                    "tokenUrl": "https://auth.example/oauth2/token",
                    "refreshUrl": "https://auth.example/oauth2/token",
                    "scopes": {"a2a-proxy-ops": "Invoke managed A2A agents via the Jarvis Registry proxy"},
                }
            },
            "oauth2MetadataUrl": "https://auth-issuer.example/.well-known/oauth-authorization-server/a2a",
        }
    }
    assert managed_card["security"] == [{"oauth2": ["a2a-proxy-ops"]}]
    assert "signatures" not in managed_card
    assert "security" not in managed_card["skills"][0]
    assert managed_card["skills"][0]["tags"] == ["weather"]
    assert managed_card["name"] == "Curated Agent"
    assert managed_card["description"] == "Curated description"
    for field in (
        "capabilities",
        "defaultInputModes",
        "defaultOutputModes",
        "provider",
        "documentationUrl",
        "iconUrl",
        "version",
        "protocolVersion",
    ):
        assert managed_card[field] == original_card[field]
    assert agent.card.model_dump(mode="json", by_alias=True, exclude_none=True) == original_card


@pytest.mark.parametrize(
    "config",
    [
        None,
        AgentConfig(title="", description="", type="jsonrpc", enabled=True),
    ],
)
def test_build_managed_agent_card_falls_back_to_origin_metadata(config: AgentConfig | None) -> None:
    agent = _managed_card_agent(config=config)

    managed_card = _service().build_managed_agent_card(agent)

    assert managed_card["name"] == "Origin Agent"
    assert managed_card["description"] == "Origin description"


def test_build_managed_agent_card_rejects_legacy_grpc_only_card() -> None:
    agent = _managed_card_agent(
        config=AgentConfig(title="Legacy", description="", type="grpc", enabled=True),
        grpc_only=True,
    )

    with pytest.raises(NoSupportedTransportError):
        _service().build_managed_agent_card(agent)


@pytest.mark.asyncio
async def test_create_agent_passes_session_to_insert():
    service = _service()
    request = AgentCreateRequest(
        path="/test-agent",
        title="Test Agent",
        description="desc",
        url="https://agent.example.com",
        type="jsonrpc",
    )
    mock_card = SimpleNamespace(version="1.0.0", name="Test Agent", description="desc")

    with (
        patch("registry.services.a2a_agent_service.A2AAgent") as MockAgent,
        patch.object(service, "_fetch_agent_card_from_url", AsyncMock(return_value=mock_card)),
    ):
        MockAgent.find_one = AsyncMock(return_value=None)  # no existing path
        agent_instance = MockAgent.return_value
        agent_instance.insert = AsyncMock()
        agent_instance.id = PydanticObjectId()
        agent_instance.config = SimpleNamespace(title="Test Agent")
        agent_instance.path = "/test-agent"

        await service.create_agent(data=request, user_id=str(PydanticObjectId()), session=_SENTINEL_SESSION)

    agent_instance.insert.assert_awaited_once()
    assert agent_instance.insert.await_args.kwargs["session"] is _SENTINEL_SESSION


@pytest.mark.asyncio
async def test_update_agent_passes_session_to_save():
    service = _service()
    fake_agent = MagicMock()
    fake_agent.save = AsyncMock()
    fake_agent.config = SimpleNamespace(title="old", url="https://agent.example.com")
    fake_agent.vectorContentHash = "hash"

    data = AgentUpdateRequest(title="New Title")  # title-only -> no card fetch

    with (
        patch("registry.services.a2a_agent_service.A2AAgent") as MockAgent,
    ):
        MockAgent.get = AsyncMock(return_value=fake_agent)

        await service.update_agent(agent_id=str(PydanticObjectId()), data=data, session=_SENTINEL_SESSION)

    fake_agent.save.assert_awaited_once()
    assert fake_agent.save.await_args.kwargs["session"] is _SENTINEL_SESSION


@pytest.mark.asyncio
async def test_update_agent_uses_card_url_fallback_to_skip_unchanged_url_fetch():
    service = _service()
    fake_agent = MagicMock()
    fake_agent.save = AsyncMock()
    fake_agent.config = SimpleNamespace(title="Old Title", description="desc", url=None, type="jsonrpc")
    fake_agent.card = SimpleNamespace(
        name="Test Agent",
        description="card desc",
        url="https://agentcore.example.com",
    )
    fake_agent.vectorContentHash = "hash"

    data = AgentUpdateRequest(title="New Title", url="https://agentcore.example.com/")
    fetch = AsyncMock()

    with (
        patch("registry.services.a2a_agent_service.A2AAgent") as MockAgent,
        patch.object(service, "_fetch_agent_card_from_url", fetch),
    ):
        MockAgent.get = AsyncMock(return_value=fake_agent)

        await service.update_agent(agent_id=str(PydanticObjectId()), data=data)

    fetch.assert_not_awaited()
    fake_agent.save.assert_awaited_once()
    assert fake_agent.config.title == "New Title"
    # config.url must stay None: it is intentionally unset for AgentCore-federated agents
    # (card.url is kept fresh by federation resync), so a no-op edit must not backfill it.
    assert fake_agent.config.url is None


@pytest.mark.asyncio
async def test_update_agent_normalizes_existing_config_url_when_unchanged():
    service = _service()
    fake_agent = MagicMock()
    fake_agent.save = AsyncMock()
    fake_agent.config = SimpleNamespace(
        title="Old Title", description="desc", url="https://agent.example.com/", type="jsonrpc"
    )
    fake_agent.card = SimpleNamespace(
        name="Test Agent",
        description="card desc",
        url="https://agent.example.com/",
    )
    fake_agent.vectorContentHash = "hash"

    data = AgentUpdateRequest(title="New Title", url="https://agent.example.com")
    fetch = AsyncMock()

    with (
        patch("registry.services.a2a_agent_service.A2AAgent") as MockAgent,
        patch.object(service, "_fetch_agent_card_from_url", fetch),
    ):
        MockAgent.get = AsyncMock(return_value=fake_agent)

        await service.update_agent(agent_id=str(PydanticObjectId()), data=data)

    fetch.assert_not_awaited()
    # Already had an explicit config.url — re-normalizing its formatting is still expected.
    assert fake_agent.config.url == "https://agent.example.com"


@pytest.mark.asyncio
async def test_update_agent_refetches_changed_card_url_with_auth_headers():
    service = A2AAgentService(a2a_agent_repo=None, jwt_config=SimpleNamespace(), azure_client_cache=MagicMock())
    old_card = SimpleNamespace(
        name="Test Agent",
        description="old desc",
        url="https://agentcore.example.com",
    )
    updated_card = SimpleNamespace(
        name="Updated Agent",
        description="new desc",
        url="https://new-agentcore.example.com",
        version="2.0.0",
    )
    fake_agent = MagicMock()
    fake_agent.save = AsyncMock()
    fake_agent.config = SimpleNamespace(title="Old Title", description="old desc", url=None, type="jsonrpc")
    fake_agent.card = old_card
    fake_agent.wellKnown = SimpleNamespace(
        enabled=True,
        lastSyncAt=datetime.now(UTC),
        lastSyncStatus="success",
        lastSyncVersion="1.0.0",
    )
    fake_agent.vectorContentHash = "hash"

    headers = {"Authorization": "Bearer agentcore-jwt"}
    fetch = AsyncMock(return_value=updated_card)

    with (
        patch("registry.services.a2a_agent_service.A2AAgent") as MockAgent,
        patch("registry.services.a2a_agent_service.build_headers", return_value=headers),
        patch.object(service, "_fetch_agent_card_from_url", fetch),
    ):
        MockAgent.get = AsyncMock(return_value=fake_agent)

        await service.update_agent(agent_id=str(PydanticObjectId()), data=AgentUpdateRequest(url=updated_card.url))

    fetch.assert_awaited_once_with(
        "https://new-agentcore.example.com",
        auth_headers=headers,
        agent_card_path_override=None,
    )
    assert fake_agent.card is updated_card
    assert fake_agent.config.url == "https://new-agentcore.example.com"
    fake_agent.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_agent_refetches_without_auth_headers_when_header_build_fails():
    service = A2AAgentService(a2a_agent_repo=None, jwt_config=SimpleNamespace(), azure_client_cache=MagicMock())
    old_card = SimpleNamespace(
        name="Test Agent",
        description="old desc",
        url="https://agentcore.example.com",
    )
    updated_card = SimpleNamespace(
        name="Updated Agent",
        description="new desc",
        url="https://new-agentcore.example.com",
        version="2.0.0",
    )
    fake_agent = MagicMock()
    fake_agent.save = AsyncMock()
    fake_agent.config = SimpleNamespace(title="Old Title", description="old desc", url=None, type="jsonrpc")
    fake_agent.card = old_card
    fake_agent.wellKnown = SimpleNamespace(
        enabled=True,
        lastSyncAt=datetime.now(UTC),
        lastSyncStatus="success",
        lastSyncVersion="1.0.0",
    )
    fake_agent.vectorContentHash = "hash"

    fetch = AsyncMock(return_value=updated_card)

    with (
        patch("registry.services.a2a_agent_service.A2AAgent") as MockAgent,
        patch("registry.services.a2a_agent_service.build_headers", side_effect=RuntimeError("jwt config invalid")),
        patch.object(service, "_fetch_agent_card_from_url", fetch),
    ):
        MockAgent.get = AsyncMock(return_value=fake_agent)

        await service.update_agent(agent_id=str(PydanticObjectId()), data=AgentUpdateRequest(url=updated_card.url))

    fetch.assert_awaited_once_with(
        "https://new-agentcore.example.com",
        auth_headers=None,
        agent_card_path_override=None,
    )
    assert fake_agent.card is updated_card
    assert fake_agent.config.url == "https://new-agentcore.example.com"
    fake_agent.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_agent_passes_session_to_delete():
    service = _service()
    fake_agent = MagicMock()
    fake_agent.delete = AsyncMock()
    fake_agent.card = SimpleNamespace(name="Test Agent")

    with (
        patch("registry.services.a2a_agent_service.A2AAgent") as MockAgent,
    ):
        MockAgent.get = AsyncMock(return_value=fake_agent)

        result = await service.delete_agent(agent_id=str(PydanticObjectId()), session=_SENTINEL_SESSION)

    assert result is True
    fake_agent.delete.assert_awaited_once()
    assert fake_agent.delete.await_args.kwargs["session"] is _SENTINEL_SESSION


@pytest.mark.asyncio
async def test_toggle_agent_status_passes_session_to_save():
    service = _service()
    fake_agent = MagicMock()
    fake_agent.save = AsyncMock()
    fake_agent.card = SimpleNamespace(name="Test Agent")
    fake_agent.vectorContentHash = "hash"

    with (
        patch("registry.services.a2a_agent_service.A2AAgent") as MockAgent,
    ):
        MockAgent.get = AsyncMock(return_value=fake_agent)

        await service.toggle_agent_status(agent_id=str(PydanticObjectId()), enabled=True, session=_SENTINEL_SESSION)

    fake_agent.save.assert_awaited_once()
    assert fake_agent.save.await_args.kwargs["session"] is _SENTINEL_SESSION


@pytest.mark.asyncio
async def test_sync_wellknown_passes_session_to_save():
    service = _service()
    old_card = SimpleNamespace(version="1.0.0", description="old", skills=[], capabilities={}, name="Test Agent")
    updated_card = SimpleNamespace(version="2.0.0", description="new", skills=[], capabilities={}, name="Test Agent")

    fake_agent = MagicMock()
    fake_agent.save = AsyncMock()
    fake_agent.card = old_card
    fake_agent.config = SimpleNamespace(url="https://agent.example.com")
    fake_agent.wellKnown = SimpleNamespace(
        enabled=True,
        lastSyncAt=datetime.now(UTC),
        lastSyncStatus="success",
        lastSyncVersion="1.0.0",
        syncError=None,
    )

    with (
        patch("registry.services.a2a_agent_service.A2AAgent") as MockAgent,
        patch.object(service, "_resolve_agent_card_with_fallback", AsyncMock(return_value=updated_card)),
    ):
        MockAgent.get = AsyncMock(return_value=fake_agent)

        await service.sync_wellknown(agent_id=str(PydanticObjectId()), session=_SENTINEL_SESSION)

    fake_agent.save.assert_awaited_once()
    assert fake_agent.save.await_args.kwargs["session"] is _SENTINEL_SESSION


# ---------------------------------------------------------------------------
# Change 4: config.url normalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://agent.example.com", "https://agent.example.com"),
        ("https://agent.example.com/", "https://agent.example.com"),
        ("https://agent.example.com/.well-known/agent-card.json", "https://agent.example.com"),
        ("https://agent.example.com/.well-known/agent.json/", "https://agent.example.com"),
        ("https://agent.example.com/.well-known", "https://agent.example.com"),
        ("https://agent.example.com/i-just-like-this", "https://agent.example.com/i-just-like-this"),
        ("https://api.example.com/.well-known-data/v1", "https://api.example.com/.well-known-data/v1"),
        (
            "https://api.example.com/api/.well-known-foo/resource",
            "https://api.example.com/api/.well-known-foo/resource",
        ),
    ],
)
def test_normalize_config_url(raw: str, expected: str):
    assert _normalize_config_url(raw) == expected


@pytest.mark.asyncio
async def test_create_agent_normalizes_config_url():
    service = _service()
    request = AgentCreateRequest(
        path="/test-agent",
        title="Test Agent",
        description="desc",
        url="https://agent.example.com/.well-known/agent-card.json",
        type="jsonrpc",
    )
    mock_card = SimpleNamespace(version="1.0.0", name="Test Agent", description="desc")
    fetch = AsyncMock(return_value=mock_card)

    with (
        patch("registry.services.a2a_agent_service.A2AAgent") as MockAgent,
        patch.object(service, "_fetch_agent_card_from_url", fetch),
    ):
        MockAgent.find_one = AsyncMock(return_value=None)
        agent_instance = MockAgent.return_value
        agent_instance.insert = AsyncMock()
        agent_instance.id = PydanticObjectId()
        agent_instance.config = SimpleNamespace(title="Test Agent")
        agent_instance.path = "/test-agent"

        await service.create_agent(data=request, user_id=str(PydanticObjectId()))

    # Discovery and the stored config.url both use the clean service root.
    fetch.assert_awaited_once_with("https://agent.example.com")
    assert str(MockAgent.call_args.kwargs["config"].url).rstrip("/") == "https://agent.example.com"


# ---------------------------------------------------------------------------
# Change 5 / 6: three-attempt fallback + auth header injection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_card_succeeds_on_third_base_url_attempt():
    service = _service()
    card = SimpleNamespace(name="Test Agent", version="1.0.0")

    resolver = MagicMock()
    resolver.get_agent_card = AsyncMock(
        side_effect=[
            A2AClientHTTPError(404, "not found"),
            A2AClientHTTPError(404, "not found"),
            card,
        ]
    )

    with (
        patch("registry.services.a2a_agent_service.httpx.AsyncClient", return_value=_AsyncCM(object())),
        patch("registry.services.a2a_agent_service.A2ACardResolver", return_value=resolver) as MockResolver,
    ):
        result = await service._resolve_agent_card_with_fallback(
            base_url="https://agent.example.com/i-just-like-this", timeout_seconds=5.0
        )

    assert result is card
    assert resolver.get_agent_card.await_count == 3
    # Third resolver is created with the empty path so it hits base_url itself.
    assert MockResolver.call_args_list[2].kwargs["agent_card_path"] == ""


@pytest.mark.asyncio
async def test_resolve_card_passes_auth_headers_to_httpx_client():
    service = _service()
    card = SimpleNamespace(name="Test Agent", version="1.0.0")
    resolver = MagicMock()
    resolver.get_agent_card = AsyncMock(return_value=card)
    headers = {"Authorization": "Bearer token-123"}

    with (
        patch("registry.services.a2a_agent_service.httpx.AsyncClient", return_value=_AsyncCM(object())) as MockClient,
        patch("registry.services.a2a_agent_service.A2ACardResolver", return_value=resolver),
    ):
        await service._resolve_agent_card_with_fallback(
            base_url="https://agent.example.com", timeout_seconds=5.0, auth_headers=headers
        )

    assert MockClient.call_args.kwargs["headers"] == headers


@pytest.mark.asyncio
async def test_sync_wellknown_builds_and_passes_auth_headers():
    service = A2AAgentService(a2a_agent_repo=None, jwt_config=SimpleNamespace(), azure_client_cache=MagicMock())
    old_card = SimpleNamespace(version="1.0.0", description="old", skills=[], capabilities={}, name="Test Agent")
    updated_card = SimpleNamespace(version="2.0.0", description="new", skills=[], capabilities={}, name="Test Agent")

    fake_agent = MagicMock()
    fake_agent.save = AsyncMock()
    fake_agent.card = old_card
    fake_agent.config = SimpleNamespace(url="https://agent.example.com")
    fake_agent.wellKnown = SimpleNamespace(
        enabled=True, lastSyncAt=datetime.now(UTC), lastSyncStatus="success", lastSyncVersion="1.0.0", syncError=None
    )

    headers = {"Authorization": "Bearer agentcore-jwt"}
    resolve = AsyncMock(return_value=updated_card)

    with (
        patch("registry.services.a2a_agent_service.A2AAgent") as MockAgent,
        patch("registry.services.a2a_agent_service.build_headers", return_value=headers),
        patch.object(service, "_resolve_agent_card_with_fallback", resolve),
    ):
        MockAgent.get = AsyncMock(return_value=fake_agent)

        await service.sync_wellknown(agent_id=str(PydanticObjectId()))

    assert resolve.await_args.kwargs["auth_headers"] == headers


def _service_with_cache(cache: MagicMock) -> A2AAgentService:
    return A2AAgentService(a2a_agent_repo=None, jwt_config=SimpleNamespace(), azure_client_cache=cache)


@pytest.mark.asyncio
async def test_build_best_effort_auth_headers_builds_azure_entra_headers_for_foundry_agent():
    federation_id = PydanticObjectId()
    agent = SimpleNamespace(
        federationMetadata=make_azure_foundry_metadata(),
        federationRefId=federation_id,
        card=SimpleNamespace(),
    )
    headers = {"Authorization": "Bearer entra-token"}
    auth = SimpleNamespace(build_headers=AsyncMock(return_value=headers))
    cache = MagicMock()
    cache.get_auth_service = AsyncMock(return_value=auth)
    service = _service_with_cache(cache)

    with patch("registry.services.a2a_agent_service.build_headers") as agentcore_headers:
        result = await service._build_best_effort_auth_headers(agent, "agent-id")

    assert result == headers
    cache.get_auth_service.assert_awaited_once_with(federation_id)
    auth.build_headers.assert_awaited_once_with()
    agentcore_headers.assert_not_called()


@pytest.mark.asyncio
async def test_build_best_effort_auth_headers_returns_none_when_federation_missing():
    federation_id = PydanticObjectId()
    agent = SimpleNamespace(
        federationMetadata=make_azure_foundry_metadata(),
        federationRefId=federation_id,
        card=SimpleNamespace(),
    )
    cache = MagicMock()
    # get_auth_service raises the same ValueError the inlined code used to (fed not found).
    cache.get_auth_service = AsyncMock(side_effect=ValueError(f"Federation {federation_id} not found"))
    service = _service_with_cache(cache)

    result = await service._build_best_effort_auth_headers(agent, "agent-id")

    assert result is None
    cache.get_auth_service.assert_awaited_once_with(federation_id)


@pytest.mark.asyncio
async def test_build_best_effort_auth_headers_returns_none_without_federation_ref_id():
    agent = SimpleNamespace(
        federationMetadata=make_azure_foundry_metadata(),
        federationRefId=None,
        card=SimpleNamespace(),
    )
    cache = MagicMock()
    cache.get_auth_service = AsyncMock()
    service = _service_with_cache(cache)

    result = await service._build_best_effort_auth_headers(agent, "agent-id")

    assert result is None
    cache.get_auth_service.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_best_effort_auth_headers_rejects_non_azure_federation():
    federation_id = PydanticObjectId()
    agent = SimpleNamespace(
        federationMetadata=make_azure_foundry_metadata(),
        federationRefId=federation_id,
        card=SimpleNamespace(),
    )
    cache = MagicMock()
    cache.get_auth_service = AsyncMock(side_effect=ValueError("is not azure_ai_foundry"))
    service = _service_with_cache(cache)

    result = await service._build_best_effort_auth_headers(agent, "agent-id")

    assert result is None
    cache.get_auth_service.assert_awaited_once_with(federation_id)


@pytest.mark.asyncio
async def test_build_best_effort_auth_headers_keeps_agentcore_path():
    cache = MagicMock()
    cache.get_auth_service = AsyncMock()
    service = _service_with_cache(cache)
    agent = SimpleNamespace(
        federationMetadata=make_agentcore_a2a_metadata(),
        federationRefId=PydanticObjectId(),
        card=SimpleNamespace(),
    )
    headers = {"Authorization": "Bearer agentcore-token"}

    with patch("registry.services.a2a_agent_service.build_headers", return_value=headers) as build:
        result = await service._build_best_effort_auth_headers(agent, "agent-id")

    assert result == headers
    build.assert_called_once_with(agent, jwt_config=service._jwt_config)
    cache.get_auth_service.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_best_effort_auth_headers_does_not_load_federation_for_manual_agent():
    cache = MagicMock()
    cache.get_auth_service = AsyncMock()
    service = _service_with_cache(cache)
    agent = SimpleNamespace(
        federationMetadata=None,
        federationRefId=None,
        card=SimpleNamespace(),
    )

    with patch("registry.services.a2a_agent_service.build_headers", return_value={}):
        result = await service._build_best_effort_auth_headers(agent, "agent-id")

    assert result == {}
    cache.get_auth_service.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_wellknown_passes_foundry_agent_card_path_override():
    service = _service()
    old_card = SimpleNamespace(version="1.0.0", description="old", skills=[], capabilities={}, name="Test Agent")
    updated_card = SimpleNamespace(version="2.0.0", description="new", skills=[], capabilities={}, name="Test Agent")
    fake_agent = SimpleNamespace(
        save=AsyncMock(),
        card=old_card,
        config=SimpleNamespace(url="https://foundry.example.com/agents/test"),
        wellKnown=SimpleNamespace(
            enabled=True,
            lastSyncAt=None,
            lastSyncStatus="pending",
            lastSyncVersion="1.0.0",
            syncError=None,
        ),
        federationMetadata=make_azure_foundry_metadata(agentCardPath="agentCard/v0.3"),
        federationRefId=PydanticObjectId(),
        updatedAt=None,
    )
    resolve = AsyncMock(return_value=updated_card)

    with (
        patch("registry.services.a2a_agent_service.A2AAgent") as MockAgent,
        patch.object(service, "_build_best_effort_auth_headers", AsyncMock(return_value={})),
        patch.object(service, "_resolve_agent_card_with_fallback", resolve),
    ):
        MockAgent.get = AsyncMock(return_value=fake_agent)
        await service.sync_wellknown(agent_id=str(PydanticObjectId()))

    assert resolve.await_args.kwargs["agent_card_path_override"] == "agentCard/v0.3"


@pytest.mark.asyncio
async def test_update_agent_passes_foundry_agent_card_path_override():
    service = _service()
    old_card = SimpleNamespace(name="Test Agent", description="old", url="https://foundry.example.com/old")
    updated_card = SimpleNamespace(
        name="Test Agent",
        description="new",
        url="https://foundry.example.com/new",
        version="2.0.0",
    )
    fake_agent = SimpleNamespace(
        save=AsyncMock(),
        card=old_card,
        config=SimpleNamespace(title="Test Agent", description="old", url=old_card.url, type="jsonrpc"),
        wellKnown=SimpleNamespace(
            enabled=True,
            lastSyncAt=None,
            lastSyncStatus="success",
            lastSyncVersion="1.0.0",
        ),
        federationMetadata=make_azure_foundry_metadata(agentCardPath="agentCard/v0.3"),
        federationRefId=PydanticObjectId(),
        vectorContentHash="hash",
        updatedAt=None,
    )
    fetch = AsyncMock(return_value=updated_card)

    with (
        patch("registry.services.a2a_agent_service.A2AAgent") as MockAgent,
        patch.object(service, "_build_best_effort_auth_headers", AsyncMock(return_value={})),
        patch.object(service, "_fetch_agent_card_from_url", fetch),
    ):
        MockAgent.get = AsyncMock(return_value=fake_agent)
        await service.update_agent(
            agent_id=str(PydanticObjectId()),
            data=AgentUpdateRequest(url=updated_card.url),
        )

    fetch.assert_awaited_once_with(
        "https://foundry.example.com/new",
        auth_headers={},
        agent_card_path_override="agentCard/v0.3",
    )


@pytest.mark.asyncio
async def test_resolve_card_with_path_override_skips_generic_well_known_paths():
    service = _service()
    card = SimpleNamespace(name="Test Agent", version="1.0.0")
    resolver = MagicMock()
    resolver.get_agent_card = AsyncMock(return_value=card)

    with (
        patch("registry.services.a2a_agent_service.httpx.AsyncClient", return_value=_AsyncCM(object())),
        patch("registry.services.a2a_agent_service.A2ACardResolver", return_value=resolver) as MockResolver,
    ):
        result = await service._resolve_agent_card_with_fallback(
            base_url="https://foundry.example.com/agents/test",
            timeout_seconds=5.0,
            agent_card_path_override="agentCard/v0.3",
        )

    assert result is card
    MockResolver.assert_called_once()
    assert MockResolver.call_args.kwargs["agent_card_path"] == "agentCard/v0.3"


def test_resolve_agent_card_path_override_falls_back_when_metadata_path_is_missing():
    agent = SimpleNamespace(
        federationMetadata=make_azure_foundry_metadata(),
    )

    assert A2AAgentService._resolve_agent_card_path_override(agent) is None
