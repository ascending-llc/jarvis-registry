from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from azure.core.credentials import AccessToken
from azure.core.exceptions import ClientAuthenticationError
from beanie import PydanticObjectId

from registry_pkgs.federation.azure_foundry_auth import AzureFoundryAuthService
from registry_pkgs.federation.azure_foundry_client_cache import AzureEntraAuth, AzureFoundryClientCache
from registry_pkgs.models.enums import FederationProviderType

_KEY = b"0" * 32
_FED_GET = "registry_pkgs.federation.azure_foundry_client_cache.Federation.get"
_CSC = "registry_pkgs.federation.azure_foundry_client_cache.ClientSecretCredential"
_DAC = "registry_pkgs.federation.azure_foundry_client_cache.DefaultAzureCredential"

_CS_CFG = {
    "projectEndpoint": "https://acc.services.ai.azure.com/api/projects/p",
    "tenantId": "tenant",
    "clientId": "client",
    "clientSecret": "plain-secret",
    "sendPreviewHeader": False,
}
_MI_CFG = {"projectEndpoint": "https://acc.services.ai.azure.com/api/projects/p", "sendPreviewHeader": False}


def _cache() -> AzureFoundryClientCache:
    return AzureFoundryClientCache(encryption_key=_KEY)


def _agent(*, federation_id: PydanticObjectId | None) -> SimpleNamespace:
    return SimpleNamespace(federationRefId=federation_id, path="test-agent")


def _federation(cfg: dict, *, updated_at: datetime | None = None, provider_type: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=PydanticObjectId(),
        providerType=provider_type or FederationProviderType.AZURE_AI_FOUNDRY.value,
        updatedAt=updated_at or datetime.now(UTC),
        providerConfig=cfg,
    )


def _csc(token: str = "tok", *, expires_in: int = 3600, fail: bool = False) -> MagicMock:
    """A ClientSecretCredential mock supporting async-context-manager + get_token."""
    cred = MagicMock()
    if fail:
        cred.get_token = AsyncMock(side_effect=ClientAuthenticationError("bad secret"))
    else:
        cred.get_token = AsyncMock(return_value=AccessToken(token, int(time.time()) + expires_in))
    cred.__aenter__ = AsyncMock(return_value=cred)
    cred.__aexit__ = AsyncMock(return_value=None)
    return cred


def _dac(token: str = "mtok", *, expires_in: int = 3600) -> MagicMock:
    cred = MagicMock()
    cred.get_token = AsyncMock(return_value=AccessToken(token, int(time.time()) + expires_in))
    cred.close = AsyncMock()
    return cred


@pytest.mark.asyncio
async def test_get_auth_service_returns_stable_facade():
    federation_id = PydanticObjectId()
    federation = _federation(_CS_CFG)
    federation.id = federation_id
    fed_get = AsyncMock(return_value=federation)
    cache = _cache()

    with patch(_FED_GET, new=fed_get):
        first = await cache.get_auth_service(federation_id)
        second = await cache.get_auth_service(federation_id)

    assert first is second
    assert isinstance(first, AzureFoundryAuthService)
    assert fed_get.await_count == 1  # state (and Federation.get) built once
    await cache.close()


@pytest.mark.asyncio
async def test_concurrent_get_auth_service_builds_state_once():
    federation_id = PydanticObjectId()
    federation = _federation(_CS_CFG)
    federation.id = federation_id
    calls = 0

    async def slow_get(_fid):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return federation

    cache = _cache()
    with patch(_FED_GET, side_effect=slow_get):
        a, b = await asyncio.gather(cache.get_auth_service(federation_id), cache.get_auth_service(federation_id))

    assert a is b
    assert calls == 1
    await cache.close()


@pytest.mark.asyncio
async def test_missing_federation_raises():
    cache = _cache()
    with patch(_FED_GET, new=AsyncMock(return_value=None)):
        with pytest.raises(ValueError, match="not found"):
            await cache.get_auth_service(PydanticObjectId())


@pytest.mark.asyncio
async def test_wrong_provider_type_raises():
    federation = _federation({}, provider_type=FederationProviderType.AWS_AGENTCORE.value)
    cache = _cache()
    with patch(_FED_GET, new=AsyncMock(return_value=federation)):
        with pytest.raises(ValueError, match="is not azure_ai_foundry"):
            await cache.get_auth_service(federation.id)


@pytest.mark.asyncio
async def test_client_secret_token_cached_no_persistent_credential():
    """AC: two sequential fetches build exactly one disposable ClientSecretCredential; token reused."""
    federation_id = PydanticObjectId()
    csc_factory = MagicMock(return_value=_csc("tok1"))
    cache = _cache()

    with patch(_FED_GET, new=AsyncMock(return_value=_federation(_CS_CFG))), patch(_CSC, new=csc_factory):
        t1 = await cache.get_access_token(federation_id)
        t2 = await cache.get_access_token(federation_id)

    assert t1.token == "tok1"
    assert t2 is t1  # cached AccessToken value reused
    assert csc_factory.call_count == 1
    # No persistent credential object retained on the state.
    assert cache._states[federation_id].long_lived_credential is None
    await cache.close()


@pytest.mark.asyncio
async def test_client_secret_token_refetched_when_near_expiry():
    federation_id = PydanticObjectId()
    csc_factory = MagicMock(side_effect=[_csc("old", expires_in=10), _csc("new", expires_in=3600)])
    cache = _cache()

    with patch(_FED_GET, new=AsyncMock(return_value=_federation(_CS_CFG))), patch(_CSC, new=csc_factory):
        t1 = await cache.get_access_token(federation_id)  # near-expiry -> not cached effectively
        t2 = await cache.get_access_token(federation_id)  # forces a fresh fetch

    assert t1.token == "old"
    assert t2.token == "new"
    assert csc_factory.call_count == 2
    await cache.close()


@pytest.mark.asyncio
async def test_concurrent_client_secret_fetch_dedups():
    """AC: two concurrent fetches with no cached token -> one credential construction + one fetch."""
    federation_id = PydanticObjectId()
    fetches = 0

    def make_cred(*a, **k):
        cred = MagicMock()

        async def slow_get_token(_scope):
            nonlocal fetches
            fetches += 1
            await asyncio.sleep(0)
            return AccessToken("tok", int(time.time()) + 3600)

        cred.get_token = slow_get_token
        cred.__aenter__ = AsyncMock(return_value=cred)
        cred.__aexit__ = AsyncMock(return_value=None)
        return cred

    csc_factory = MagicMock(side_effect=make_cred)
    cache = _cache()
    with patch(_FED_GET, new=AsyncMock(return_value=_federation(_CS_CFG))), patch(_CSC, new=csc_factory):
        a, b = await asyncio.gather(cache.get_access_token(federation_id), cache.get_access_token(federation_id))

    assert a is b
    assert fetches == 1
    assert csc_factory.call_count == 1
    await cache.close()


@pytest.mark.asyncio
async def test_build_headers_includes_preview_when_opted_in():
    federation_id = PydanticObjectId()
    cfg = {**_CS_CFG, "sendPreviewHeader": True}
    cache = _cache()
    with (
        patch(_FED_GET, new=AsyncMock(return_value=_federation(cfg))),
        patch(_CSC, new=MagicMock(return_value=_csc("t"))),
    ):
        headers = await cache.build_headers(federation_id)
    assert headers["Authorization"] == "Bearer t"
    assert headers["Foundry-Features"] == "HostedAgents=V1Preview"
    await cache.close()


@pytest.mark.asyncio
async def test_managed_identity_credential_built_once_sequential():
    """AC: DefaultAzureCredential is constructed once and reused across N calls."""
    federation_id = PydanticObjectId()
    dac_factory = MagicMock(return_value=_dac("mtok"))
    cache = _cache()

    with patch(_FED_GET, new=AsyncMock(return_value=_federation(_MI_CFG))), patch(_DAC, new=dac_factory):
        t1 = await cache.get_access_token(federation_id)
        t2 = await cache.get_access_token(federation_id)
        t3 = await cache.get_access_token(federation_id)

    assert t1.token == t2.token == t3.token == "mtok"
    assert dac_factory.call_count == 1
    await cache.close()


@pytest.mark.asyncio
async def test_managed_identity_credential_built_once_concurrent():
    federation_id = PydanticObjectId()
    dac_factory = MagicMock(side_effect=lambda *a, **k: _dac("mtok"))
    cache = _cache()

    with patch(_FED_GET, new=AsyncMock(return_value=_federation(_MI_CFG))), patch(_DAC, new=dac_factory):
        await cache.get_auth_service(federation_id)  # build state first (serialized)
        await asyncio.gather(cache.get_access_token(federation_id), cache.get_access_token(federation_id))

    assert dac_factory.call_count == 1
    await cache.close()


@pytest.mark.asyncio
async def test_unchanged_updatedat_propagates_without_rebuild():
    """AC: bad secret + unchanged updatedAt -> original error propagates, no second credential."""
    federation_id = PydanticObjectId()
    same = datetime.now(UTC)
    csc_factory = MagicMock(side_effect=[_csc(fail=True)])
    cache = _cache()

    with (
        patch(_FED_GET, new=AsyncMock(return_value=_federation(_CS_CFG, updated_at=same))),
        patch(_CSC, new=csc_factory),
    ):
        with pytest.raises(ClientAuthenticationError):
            await cache.get_access_token(federation_id)

    assert csc_factory.call_count == 1  # no rebuild
    await cache.close()


@pytest.mark.asyncio
async def test_changed_updatedat_rebuilds_and_heals_rotated_secret():
    """AC: worker with no invalidate() picks up a rotated clientSecret via updatedAt bump."""
    federation_id = PydanticObjectId()
    old = datetime.now(UTC)
    new = old + timedelta(seconds=5)
    fed_get = AsyncMock(
        side_effect=[
            _federation(_CS_CFG, updated_at=old),
            _federation({**_CS_CFG, "clientSecret": "rotated"}, updated_at=new),
        ]
    )
    csc_factory = MagicMock(side_effect=[_csc(fail=True), _csc("healed")])
    cache = _cache()

    with patch(_FED_GET, new=fed_get), patch(_CSC, new=csc_factory):
        token = await cache.get_access_token(federation_id)

    assert token.token == "healed"
    assert csc_factory.call_count == 2  # bounded: exactly one rebuild
    assert fed_get.await_count == 2  # initial build + one refresh
    await cache.close()


@pytest.mark.asyncio
async def test_rebuild_bounded_to_one_when_secret_still_bad():
    """AC: even with updatedAt advanced, a still-bad new secret is not retried past one rebuild."""
    federation_id = PydanticObjectId()
    old = datetime.now(UTC)
    new = old + timedelta(seconds=5)
    fed_get = AsyncMock(
        side_effect=[
            _federation(_CS_CFG, updated_at=old),
            _federation({**_CS_CFG, "clientSecret": "still-bad"}, updated_at=new),
        ]
    )
    csc_factory = MagicMock(side_effect=[_csc(fail=True), _csc(fail=True)])
    cache = _cache()

    with patch(_FED_GET, new=fed_get), patch(_CSC, new=csc_factory):
        with pytest.raises(ClientAuthenticationError):
            await cache.get_access_token(federation_id)

    assert csc_factory.call_count == 2  # one rebuild only, then surfaces
    await cache.close()


@pytest.mark.asyncio
async def test_rebuild_mode_switch_dispatches_managed_identity():
    """AC: a rebuild that clears clientSecret flips to managed_identity and succeeds via DefaultAzureCredential."""
    federation_id = PydanticObjectId()
    old = datetime.now(UTC)
    new = old + timedelta(seconds=5)
    fed_get = AsyncMock(side_effect=[_federation(_CS_CFG, updated_at=old), _federation(_MI_CFG, updated_at=new)])
    csc_factory = MagicMock(side_effect=[_csc(fail=True)])
    dac_factory = MagicMock(return_value=_dac("managed-tok"))
    cache = _cache()

    with patch(_FED_GET, new=fed_get), patch(_CSC, new=csc_factory), patch(_DAC, new=dac_factory):
        token = await cache.get_access_token(federation_id)

    assert token.token == "managed-tok"
    assert csc_factory.call_count == 1  # only the failing client_secret fetch
    assert dac_factory.call_count == 1  # switched to managed identity
    assert cache._states[federation_id].mode == "managed_identity"
    await cache.close()


@pytest.mark.asyncio
async def test_invalidate_client_secret_drops_cached_token():
    federation_id = PydanticObjectId()
    csc_factory = MagicMock(side_effect=[_csc("first"), _csc("second")])
    cache = _cache()

    with patch(_FED_GET, new=AsyncMock(return_value=_federation(_CS_CFG))), patch(_CSC, new=csc_factory):
        t1 = await cache.get_access_token(federation_id)
        await cache.invalidate(federation_id)
        t2 = await cache.get_access_token(federation_id)

    assert t1.token == "first"
    assert t2.token == "second"  # re-fetched after invalidate
    assert csc_factory.call_count == 2
    await cache.close()


@pytest.mark.asyncio
async def test_invalidate_managed_closes_and_rebuilds_credential():
    """Adjusted behavior: invalidate closes the managed credential and rebuilds on next use."""
    federation_id = PydanticObjectId()
    first = _dac("m1")
    second = _dac("m2")
    dac_factory = MagicMock(side_effect=[first, second])
    cache = _cache()

    with patch(_FED_GET, new=AsyncMock(return_value=_federation(_MI_CFG))), patch(_DAC, new=dac_factory):
        await cache.get_access_token(federation_id)
        await cache.invalidate(federation_id)
        await cache.get_access_token(federation_id)

    first.close.assert_awaited_once()  # old managed credential closed on invalidate (no leak)
    assert dac_factory.call_count == 2
    await cache.close()


@pytest.mark.asyncio
async def test_invalidate_mode_switch_client_secret_to_managed():
    federation_id = PydanticObjectId()
    fed_get = AsyncMock(side_effect=[_federation(_CS_CFG), _federation(_MI_CFG)])
    csc_factory = MagicMock(return_value=_csc("cs"))
    dac_factory = MagicMock(return_value=_dac("mi"))
    cache = _cache()

    with patch(_FED_GET, new=fed_get), patch(_CSC, new=csc_factory), patch(_DAC, new=dac_factory):
        t1 = await cache.get_access_token(federation_id)  # client_secret
        await cache.invalidate(federation_id)
        t2 = await cache.get_access_token(federation_id)  # managed after config now clears secret

    assert t1.token == "cs"
    assert t2.token == "mi"
    assert cache._states[federation_id].mode == "managed_identity"
    await cache.close()


@pytest.mark.asyncio
async def test_get_client_reuses_client_and_facade():
    federation_id = PydanticObjectId()
    federation = _federation(_CS_CFG)
    federation.id = federation_id
    fed_get = AsyncMock(return_value=federation)
    cache = _cache()

    with patch(_FED_GET, new=fed_get):
        client = await cache.get_client(_agent(federation_id=federation_id))
        same = await cache.get_client(_agent(federation_id=federation_id))

    assert same is client
    assert isinstance(client.auth, AzureEntraAuth)
    assert client.auth._auth_service is cache._facades[federation_id]
    assert fed_get.await_count == 1
    await cache.close()


@pytest.mark.asyncio
async def test_azure_agent_without_federation_ref_raises():
    cache = _cache()
    with pytest.raises(ValueError, match="federationRefId"):
        await cache.get_client(_agent(federation_id=None))


@pytest.mark.asyncio
async def test_invalidate_cannot_interleave_get_client_build():
    """Regression: get_client builds state + client under one lock; invalidate cannot slip between."""
    federation_id = PydanticObjectId()
    federation = _federation(_CS_CFG)
    federation.id = federation_id
    build_started = asyncio.Event()
    release = asyncio.Event()

    async def slow_get(_fid):
        build_started.set()
        await release.wait()
        return federation

    cache = _cache()
    with patch(_FED_GET, side_effect=slow_get):
        get_task = asyncio.create_task(cache.get_client(_agent(federation_id=federation_id)))
        await build_started.wait()
        inv_task = asyncio.create_task(cache.invalidate(federation_id))
        await asyncio.sleep(0)
        assert not inv_task.done(), "invalidate must block on the build lock"
        release.set()
        client = await get_task
        await inv_task

    assert client.is_closed
    assert federation_id not in cache._clients
    assert federation_id not in cache._states
    await cache.close()


@pytest.mark.asyncio
async def test_close_closes_clients_and_managed_credentials():
    federation_id = PydanticObjectId()
    managed = _dac("m")
    cache = _cache()

    with (
        patch(_FED_GET, new=AsyncMock(return_value=_federation(_MI_CFG))),
        patch(_DAC, new=MagicMock(return_value=managed)),
    ):
        await cache.get_access_token(federation_id)  # builds managed credential
        client = await cache.get_client(_agent(federation_id=federation_id))
        await cache.close()

    assert client.is_closed
    managed.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_azure_entra_auth_injects_headers():
    auth_service = MagicMock()
    auth_service.build_headers = AsyncMock(return_value={"Authorization": "Bearer entra-token"})
    auth = AzureEntraAuth(auth_service)
    request = httpx.Request("POST", "https://agent.example.com")

    authed = await anext(auth.async_auth_flow(request))

    assert authed.headers["Authorization"] == "Bearer entra-token"
    auth_service.build_headers.assert_awaited_once()
