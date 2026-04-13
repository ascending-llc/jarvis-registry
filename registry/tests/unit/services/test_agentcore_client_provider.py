from unittest.mock import MagicMock

import pytest

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
