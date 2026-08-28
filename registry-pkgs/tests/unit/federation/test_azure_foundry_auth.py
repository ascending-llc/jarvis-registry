from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from azure.core.exceptions import ClientAuthenticationError

from registry_pkgs.core.crypto_utils import encrypt_value
from registry_pkgs.federation.azure_foundry_auth import AzureFoundryAuthService
from registry_pkgs.models.federation import AzureAiFoundryProviderConfig

_KEY = b"0" * 32

_CRED_PATH = "registry_pkgs.federation.azure_foundry_auth.ClientSecretCredential"
_DEFAULT_CRED_PATH = "registry_pkgs.federation.azure_foundry_auth.DefaultAzureCredential"


def _config(**overrides) -> AzureAiFoundryProviderConfig:
    defaults = {
        "projectEndpoint": "https://acc.services.ai.azure.com/api/projects/p",
        "tenantId": "tenant",
        "clientId": "client",
        "clientSecret": "plain-secret",
        "sendPreviewHeader": False,
    }
    defaults.update(overrides)
    return AzureAiFoundryProviderConfig(**defaults)


def _fake_cred(token: str = "tok") -> SimpleNamespace:
    return SimpleNamespace(
        get_token=AsyncMock(return_value=SimpleNamespace(token=token)),
        close=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_access_token_uses_ai_azure_scope():
    fake_cred = _fake_cred()
    with patch(_CRED_PATH, return_value=fake_cred) as cred_cls:
        async with AzureFoundryAuthService(_config(), encryption_key=_KEY) as auth:
            token = await auth.access_token()

    assert token == "tok"
    cred_cls.assert_called_once_with(tenant_id="tenant", client_id="client", client_secret="plain-secret")
    fake_cred.get_token.assert_awaited_once_with("https://ai.azure.com/.default")
    fake_cred.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_build_headers_default_does_not_send_preview_header():
    with patch(_CRED_PATH, return_value=_fake_cred()):
        auth = AzureFoundryAuthService(_config(sendPreviewHeader=False), encryption_key=_KEY)
        headers = await auth.build_headers()
        await auth.close()

    assert headers == {"Authorization": "Bearer tok"}


@pytest.mark.asyncio
async def test_build_headers_includes_preview_header_when_opted_in():
    with patch(_CRED_PATH, return_value=_fake_cred()):
        auth = AzureFoundryAuthService(_config(sendPreviewHeader=True), encryption_key=_KEY)
        headers = await auth.build_headers()
        await auth.close()

    assert headers["Authorization"] == "Bearer tok"
    assert headers["Foundry-Features"] == "HostedAgents=V1Preview"


@pytest.mark.asyncio
async def test_credential_decrypts_encrypted_secret():
    ciphertext = encrypt_value("plain-secret", encryption_key=_KEY)
    captured: dict[str, object] = {}

    def _capture(*, tenant_id, client_id, client_secret):
        captured["client_secret"] = client_secret
        return _fake_cred()

    with patch(_CRED_PATH, side_effect=_capture):
        auth = AzureFoundryAuthService(_config(clientSecret=ciphertext), encryption_key=_KEY)
        await auth.access_token()
        await auth.close()

    assert captured["client_secret"] == "plain-secret"


@pytest.mark.asyncio
async def test_no_secret_falls_back_to_default_azure_credential():
    fake_cred = _fake_cred()
    with patch(_DEFAULT_CRED_PATH, return_value=fake_cred) as cred_cls:
        auth = AzureFoundryAuthService(_config(clientSecret=""), encryption_key=_KEY)
        token = await auth.access_token()
        await auth.close()

    assert token == "tok"
    cred_cls.assert_called_once_with()
    fake_cred.get_token.assert_awaited_once_with("https://ai.azure.com/.default")


@pytest.mark.asyncio
async def test_secret_without_tenant_or_client_raises():
    auth = AzureFoundryAuthService(_config(clientSecret="plain-secret", tenantId="", clientId=""), encryption_key=_KEY)
    with pytest.raises(ValueError, match="tenantId and clientId"):
        await auth.access_token()


@pytest.mark.asyncio
async def test_self_heal_rebuilds_once_and_retries_on_auth_error():
    dead = SimpleNamespace(get_token=AsyncMock(side_effect=ClientAuthenticationError("boom")), close=AsyncMock())
    healthy = _fake_cred()
    with patch(_CRED_PATH, side_effect=[dead, healthy]) as cred_cls:
        auth = AzureFoundryAuthService(_config(), encryption_key=_KEY)
        token = await auth.access_token()
        await auth.close()

    assert token == "tok"
    assert cred_cls.call_count == 2
    dead.close.assert_awaited()


@pytest.mark.asyncio
async def test_self_heal_surfaces_error_when_secret_still_bad():
    bad1 = SimpleNamespace(get_token=AsyncMock(side_effect=ClientAuthenticationError("bad")), close=AsyncMock())
    bad2 = SimpleNamespace(get_token=AsyncMock(side_effect=ClientAuthenticationError("bad")), close=AsyncMock())
    with patch(_CRED_PATH, side_effect=[bad1, bad2]) as cred_cls:
        auth = AzureFoundryAuthService(_config(), encryption_key=_KEY)
        with pytest.raises(ClientAuthenticationError):
            await auth.access_token()
        await auth.close()

    assert cred_cls.call_count == 2
