from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from registry.services.federation.azure_foundry_auth import AzureFoundryAuthService
from registry_pkgs.models.federation import AzureAiFoundryProviderConfig


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


@pytest.mark.asyncio
async def test_access_token_uses_ai_azure_scope():
    fake_cred = SimpleNamespace(
        get_token=AsyncMock(return_value=SimpleNamespace(token="tok")),
        close=AsyncMock(),
    )
    with patch(
        "registry.services.federation.azure_foundry_auth.ClientSecretCredential",
        return_value=fake_cred,
    ) as cred_cls:
        async with AzureFoundryAuthService(_config()) as auth:
            token = await auth.access_token()

    assert token == "tok"
    cred_cls.assert_called_once_with(
        tenant_id="tenant",
        client_id="client",
        client_secret="plain-secret",
    )
    fake_cred.get_token.assert_awaited_once_with("https://ai.azure.com/.default")
    fake_cred.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_build_headers_default_does_not_send_preview_header():
    fake_cred = SimpleNamespace(
        get_token=AsyncMock(return_value=SimpleNamespace(token="tok")),
        close=AsyncMock(),
    )
    with patch(
        "registry.services.federation.azure_foundry_auth.ClientSecretCredential",
        return_value=fake_cred,
    ):
        auth = AzureFoundryAuthService(_config(sendPreviewHeader=False))
        headers = await auth.build_headers()
        await auth.close()

    assert headers == {"Authorization": "Bearer tok"}


@pytest.mark.asyncio
async def test_build_headers_includes_preview_header_when_opted_in():
    fake_cred = SimpleNamespace(
        get_token=AsyncMock(return_value=SimpleNamespace(token="tok")),
        close=AsyncMock(),
    )
    with patch(
        "registry.services.federation.azure_foundry_auth.ClientSecretCredential",
        return_value=fake_cred,
    ):
        auth = AzureFoundryAuthService(_config(sendPreviewHeader=True))
        headers = await auth.build_headers()
        await auth.close()

    assert headers["Authorization"] == "Bearer tok"
    assert headers["Foundry-Features"] == "HostedAgents=V1Preview"


@pytest.mark.asyncio
async def test_credential_decrypts_encrypted_secret(monkeypatch):
    # Provide an encryption key so encrypt_value can succeed and is_encrypted
    # recognises the colon-separated format that decrypt_value undoes.
    from registry.core.config import settings as registry_settings
    from registry.utils.crypto_utils import encrypt_value

    monkeypatch.setattr(registry_settings, "encryption_key", b"0" * 32, raising=False)
    ciphertext = encrypt_value("plain-secret")

    captured: dict[str, object] = {}

    def _capture(*, tenant_id, client_id, client_secret):
        captured["client_secret"] = client_secret
        return SimpleNamespace(
            get_token=AsyncMock(return_value=SimpleNamespace(token="tok")),
            close=AsyncMock(),
        )

    with patch(
        "registry.services.federation.azure_foundry_auth.ClientSecretCredential",
        side_effect=_capture,
    ):
        auth = AzureFoundryAuthService(_config(clientSecret=ciphertext))
        await auth.access_token()
        await auth.close()

    assert captured["client_secret"] == "plain-secret"


@pytest.mark.asyncio
async def test_missing_credentials_raises_before_calling_sdk():
    auth = AzureFoundryAuthService(_config(clientSecret=""))
    with pytest.raises(ValueError, match="tenantId, clientId and clientSecret"):
        await auth.access_token()
