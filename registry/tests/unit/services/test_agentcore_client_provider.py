import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest
from botocore.exceptions import ClientError

from registry.services.federation.agentcore_clients import AgentCoreClientProvider


@pytest.mark.asyncio
async def test_get_control_client_does_not_cache_assume_role_clients(monkeypatch):
    provider = AgentCoreClientProvider()
    created_clients = [object(), object()]
    build_mock = MagicMock(side_effect=created_clients)
    monkeypatch.setattr(provider, "_build_control_client", build_mock)

    client_one = await provider.get_control_client("us-east-1", "arn:aws:iam::123456789012:role/RoleOne")
    client_two = await provider.get_control_client("us-east-1", "arn:aws:iam::123456789012:role/RoleOne")

    assert client_one is created_clients[0]
    assert client_two is created_clients[1]
    assert build_mock.call_count == 2


@pytest.mark.asyncio
async def test_get_control_client_uses_current_assume_role_arn_for_each_request(monkeypatch):
    provider = AgentCoreClientProvider()
    build_mock = MagicMock(side_effect=[object(), object()])
    monkeypatch.setattr(provider, "_build_control_client", build_mock)

    await provider.get_control_client("us-east-1", "arn:aws:iam::123456789012:role/RoleOne")
    await provider.get_control_client("us-east-1", "arn:aws:iam::123456789012:role/RoleTwo")

    assert build_mock.call_args_list == [
        (("us-east-1", "arn:aws:iam::123456789012:role/RoleOne"),),
        (("us-east-1", "arn:aws:iam::123456789012:role/RoleTwo"),),
    ]


@pytest.mark.asyncio
async def test_get_runtime_credentials_provider_does_not_cache_assume_role_sessions(monkeypatch):
    provider = AgentCoreClientProvider()
    session_one = MagicMock()
    session_one.get_credentials.return_value = "creds-one"
    session_two = MagicMock()
    session_two.get_credentials.return_value = "creds-two"
    create_session_mock = MagicMock(side_effect=[session_one, session_two])
    monkeypatch.setattr(provider, "_create_session", create_session_mock)

    provider_one = await provider.get_runtime_credentials_provider(
        "us-east-1",
        "arn:aws:iam::123456789012:role/RoleOne",
    )
    provider_two = await provider.get_runtime_credentials_provider(
        "us-east-1",
        "arn:aws:iam::123456789012:role/RoleOne",
    )

    assert provider_one() == "creds-one"
    assert provider_two() == "creds-two"
    assert create_session_mock.call_count == 2


@pytest.mark.asyncio
async def test_get_control_client_caches_default_chain_clients(monkeypatch):
    provider = AgentCoreClientProvider()
    session = MagicMock()
    client = object()
    session.client.return_value = client
    create_session_mock = MagicMock(return_value=session)
    monkeypatch.setattr(provider, "_create_session", create_session_mock)

    client_one = await provider.get_control_client("us-east-1")
    client_two = await provider.get_control_client("us-east-1")

    assert client_one is client
    assert client_two is client
    create_session_mock.assert_called_once_with("us-east-1", None)


@pytest.mark.asyncio
async def test_scoped_control_executor_coalesces_concurrent_token_refresh(monkeypatch):
    provider = AgentCoreClientProvider()
    stale_client = object()
    refreshed_client = object()
    executor = provider.create_scoped_control_executor(
        region="us-east-1",
        assume_role_arn="arn:aws:iam::123456789012:role/RoleOne",
        client=stale_client,
    )
    barrier = threading.Barrier(2)

    def _operation(client):
        if client is stale_client:
            barrier.wait(timeout=1)
            raise ClientError(
                error_response={"Error": {"Code": "ExpiredTokenException", "Message": "expired"}},
                operation_name="GetAgentRuntime",
            )
        assert client is refreshed_client
        return "ok"

    invalidate = AsyncMock()
    get_control_client = AsyncMock(return_value=refreshed_client)
    monkeypatch.setattr(provider, "invalidate_context", invalidate)
    monkeypatch.setattr(provider, "get_control_client", get_control_client)

    results = await asyncio.gather(executor.execute(_operation), executor.execute(_operation))

    assert results == ["ok", "ok"]
    invalidate.assert_awaited_once()
    get_control_client.assert_awaited_once_with(
        "us-east-1",
        "arn:aws:iam::123456789012:role/RoleOne",
    )


@pytest.mark.asyncio
async def test_scoped_control_executor_propagates_non_token_error() -> None:
    provider = AgentCoreClientProvider()
    executor = provider.create_scoped_control_executor(
        region="us-east-1",
        assume_role_arn=None,
        client=object(),
    )

    with pytest.raises(RuntimeError, match="operation failed"):
        await executor.execute(lambda _client: (_ for _ in ()).throw(RuntimeError("operation failed")))


@pytest.mark.asyncio
async def test_runtime_client_and_credentials_provider_use_initialized_cache(monkeypatch) -> None:
    provider = AgentCoreClientProvider()
    cache_key = ("us-east-1", "")
    runtime_client = object()

    def credentials_provider() -> str:
        return "credentials"

    async def _initialize(_region, _assume_role_arn=None) -> None:
        provider._runtime_clients[cache_key] = runtime_client
        provider._credential_providers[cache_key] = credentials_provider

    monkeypatch.setattr(provider, "_initialize_context", _initialize)

    assert await provider.get_runtime_client("us-east-1") is runtime_client
    assert await provider.get_runtime_credentials_provider("us-east-1") is credentials_provider
    assert await provider.get_runtime_client("us-east-1") is runtime_client
    assert await provider.get_runtime_credentials_provider("us-east-1") is credentials_provider


@pytest.mark.asyncio
async def test_invalidate_context_clears_all_cached_state() -> None:
    provider = AgentCoreClientProvider()
    cache_key = ("us-east-1", "")
    provider._control_clients[cache_key] = object()
    provider._runtime_clients[cache_key] = object()
    provider._credential_providers[cache_key] = object()
    provider._sessions[cache_key] = object()

    await provider.invalidate_context("us-east-1")

    assert cache_key not in provider._control_clients
    assert cache_key not in provider._runtime_clients
    assert cache_key not in provider._credential_providers
    assert cache_key not in provider._sessions


@pytest.mark.asyncio
async def test_execute_with_control_client_refreshes_expired_token(monkeypatch) -> None:
    provider = AgentCoreClientProvider()
    stale_client = object()
    refreshed_client = object()
    monkeypatch.setattr(
        provider,
        "get_control_client",
        AsyncMock(side_effect=[stale_client, refreshed_client]),
    )
    invalidate = AsyncMock()
    monkeypatch.setattr(provider, "invalidate_context", invalidate)

    def _operation(client):
        if client is stale_client:
            raise ClientError(
                error_response={"Error": {"Code": "ExpiredToken", "Message": "expired"}},
                operation_name="ListAgentRuntimes",
            )
        return "ok"

    assert await provider.execute_with_control_client("us-east-1", _operation) == "ok"
    invalidate.assert_awaited_once_with("us-east-1", None)


@pytest.mark.asyncio
async def test_execute_with_runtime_client_refreshes_expired_token(monkeypatch) -> None:
    provider = AgentCoreClientProvider()
    stale_client = object()
    refreshed_client = object()
    monkeypatch.setattr(
        provider,
        "get_runtime_client",
        AsyncMock(side_effect=[stale_client, refreshed_client]),
    )
    invalidate = AsyncMock()
    monkeypatch.setattr(provider, "invalidate_context", invalidate)

    async def _operation(client):
        if client is stale_client:
            raise ClientError(
                error_response={"Error": {"Code": "RequestExpired", "Message": "expired"}},
                operation_name="GetAgentCard",
            )
        return "ok"

    assert await provider.execute_with_runtime_client("us-east-1", _operation) == "ok"
    invalidate.assert_awaited_once_with("us-east-1", None)
