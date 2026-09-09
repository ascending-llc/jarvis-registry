from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId
from httpx import Response

from registry.services.skill_sync_oauth_service import SkillSyncOAuthService
from registry.utils.crypto_utils import encrypt_value


def _source():
    return SimpleNamespace(
        id=PydanticObjectId(),
        githubAppClientId="client-id",
        githubAppClientSecretEncrypted=encrypt_value("client-secret"),
    )


def test_authorization_url_uses_pkce_and_persists_flow() -> None:
    flow_manager = MagicMock()
    flow_manager.create_flow_metadata.return_value = SimpleNamespace(state="encoded-state", authorization_url="")
    service = SkillSyncOAuthService(
        flow_state_manager=flow_manager,
        token_service=MagicMock(),
        http_client=MagicMock(),
    )

    url = service.create_authorization_url(
        source=_source(),
        user_id="000000000000000000000001",
        redirect_uri="https://registry.example/callback",
    )

    assert "code_challenge=" in url
    assert "code_challenge_method=S256" in url
    assert "state=encoded-state" in url
    flow_manager.create_flow.assert_called_once()
    assert "include_client_secret" not in flow_manager.create_flow_metadata.call_args.kwargs


def test_resolve_source_id_returns_flow_server_id() -> None:
    flow_manager = MagicMock()
    flow_manager.decode_state.return_value = {"flow_id": "flow", "security_token": "token"}
    flow_manager.get_flow.return_value = SimpleNamespace(server_id="source-123", state="state")
    service = SkillSyncOAuthService(
        flow_state_manager=flow_manager,
        token_service=MagicMock(),
        http_client=MagicMock(),
    )

    assert service.resolve_source_id("state") == "source-123"
    flow_manager.get_flow.assert_called_once_with("flow")


def test_resolve_source_id_raises_when_flow_missing() -> None:
    flow_manager = MagicMock()
    flow_manager.decode_state.return_value = {"flow_id": "flow", "security_token": "token"}
    flow_manager.get_flow.return_value = None
    service = SkillSyncOAuthService(
        flow_state_manager=flow_manager,
        token_service=MagicMock(),
        http_client=MagicMock(),
    )

    with pytest.raises(ValueError):
        service.resolve_source_id("state")


def test_resolve_source_id_raises_when_state_does_not_match_flow() -> None:
    flow_manager = MagicMock()
    flow_manager.decode_state.return_value = {"flow_id": "flow", "security_token": "token"}
    flow_manager.get_flow.return_value = SimpleNamespace(server_id="source-123", state="the-real-state")
    service = SkillSyncOAuthService(
        flow_state_manager=flow_manager,
        token_service=MagicMock(),
        http_client=MagicMock(),
    )

    with pytest.raises(ValueError):
        service.resolve_source_id("a-forged-state")


def test_resolve_source_id_propagates_malformed_state() -> None:
    flow_manager = MagicMock()
    flow_manager.decode_state.side_effect = ValueError("bad state")
    service = SkillSyncOAuthService(
        flow_state_manager=flow_manager,
        token_service=MagicMock(),
        http_client=MagicMock(),
    )

    with pytest.raises(ValueError):
        service.resolve_source_id("state")


@pytest.mark.asyncio
async def test_callback_consumes_state_and_stores_tokens() -> None:
    source = _source()
    flow_manager = MagicMock()
    flow_manager.decode_state.return_value = {"flow_id": "flow", "security_token": "token"}
    flow_manager.consume_flow.return_value = SimpleNamespace(
        server_id=str(source.id),
        user_id="000000000000000000000001",
        code_verifier="verifier",
    )
    response = Response(
        200,
        json={"access_token": "access", "refresh_token": "refresh", "expires_in": 3600},
        request=MagicMock(),
    )
    http_client = MagicMock()
    http_client.post = AsyncMock(return_value=response)
    token_service = MagicMock()
    token_service.store_tokens = AsyncMock()
    service = SkillSyncOAuthService(
        flow_state_manager=flow_manager,
        token_service=token_service,
        http_client=http_client,
    )

    user_id = await service.exchange_callback(
        source=source,
        code="code",
        state="state",
        redirect_uri="https://registry.example/callback",
    )

    assert user_id == "000000000000000000000001"
    token_service.store_tokens.assert_awaited_once()
    assert token_service.store_tokens.await_args.kwargs["tokens"].access_token == "access"
