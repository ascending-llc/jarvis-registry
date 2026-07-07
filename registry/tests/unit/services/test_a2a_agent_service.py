from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from a2a.client import A2AClientHTTPError
from beanie import PydanticObjectId

from registry.schemas.a2a_agent_api_schemas import AgentCreateRequest, AgentUpdateRequest
from registry.services.a2a_agent_service import A2AAgentService, _normalize_config_url

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
    return A2AAgentService(a2a_agent_repo=None)


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
    service = A2AAgentService(a2a_agent_repo=None, jwt_config=SimpleNamespace())
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

    fetch.assert_awaited_once_with("https://new-agentcore.example.com", auth_headers=headers)
    assert fake_agent.card is updated_card
    assert fake_agent.config.url == "https://new-agentcore.example.com"
    fake_agent.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_agent_refetches_without_auth_headers_when_header_build_fails():
    service = A2AAgentService(a2a_agent_repo=None, jwt_config=SimpleNamespace())
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

    fetch.assert_awaited_once_with("https://new-agentcore.example.com", auth_headers=None)
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
    service = A2AAgentService(a2a_agent_repo=None, jwt_config=SimpleNamespace())
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
