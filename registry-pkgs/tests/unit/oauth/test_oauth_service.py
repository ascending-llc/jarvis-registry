"""Unit tests for MCPOAuthService.

Most tests run the real service against in-memory fakes of the three things it talks to: the
``mcpservers`` collection (``registryOAuth`` compare-and-swap writes), the token store, and MCP
OAuth discovery. The OAuth client's network calls (registration, token endpoint) are mocks.
"""

import asyncio
import copy
import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from beanie import PydanticObjectId
from redis.exceptions import ConnectionError as RedisConnectionError

from registry_pkgs.core.crypto_utils import decrypt_value
from registry_pkgs.core.exceptions import DownstreamAuthRejectedException
from registry_pkgs.models.extended_mcp_server import ExtendedMCPServer
from registry_pkgs.models.mcp_server_oauth import RegistryOAuthClient, RegistryOAuthState
from registry_pkgs.oauth import FlowStateManager
from registry_pkgs.oauth import refresh_lock as refresh_lock_module
from registry_pkgs.oauth.discovery import DiscoveryResult
from registry_pkgs.oauth.errors import (
    OAuthDiscoveryError,
    OAuthReAuthRequiredError,
    OAuthTokenEndpointError,
    RegistryOAuthStateConflictError,
)
from registry_pkgs.oauth.oauth_service import ENSURE_STATE_MAX_ATTEMPTS, MCPOAuthService
from registry_pkgs.oauth.oauth_utils import get_default_redirect_uri
from registry_pkgs.oauth.refresh_lock import refresh_lock
from registry_pkgs.oauth.schemas import (
    OAuthClientInformation,
    OAuthFlowStatus,
    OAuthFlowStatusResponse,
    OAuthTokens,
)
from registry_pkgs.oauth.token_service import TokenService

FIXTURES = Path(__file__).parent / "fixtures"
KEY = bytes.fromhex("00" * 16)
BASE_URL = "http://localhost:7860"
USER = "user-1"
SERVER_ID = PydanticObjectId("65f000000000000000000001")
ATLASSIAN_URL = "https://mcp.atlassian.com/v2/mcp"
ATLASSIAN_ISSUER = "https://auth.atlassian.com/VCeDsk8ZHncYF1g234fKtc4lNipbBhu3"


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


def atlassian_discovery(**overrides: Any) -> DiscoveryResult:
    prm = _load("atlassian_v2_prm.json")
    values: dict[str, Any] = {
        "server_url": ATLASSIAN_URL,
        "resource": ATLASSIAN_URL,
        "protected_resource_metadata": prm,
        "authorization_server_metadata": _load("atlassian_v2_as_metadata.json"),
        "issuer": ATLASSIAN_ISSUER,
        "scope": " ".join(prm["scopes_supported"]),
    }
    values.update(overrides)
    return DiscoveryResult(**values)


def changed_discovery() -> DiscoveryResult:
    as_metadata = {
        **_load("atlassian_v2_as_metadata.json"),
        "issuer": "https://auth.atlassian.com/NEW",
        "registration_endpoint": "https://auth.atlassian.com/NEW/dcr/register",
        "token_endpoint": "https://auth.atlassian.com/NEW/oauth/token",
    }
    return atlassian_discovery(issuer="https://auth.atlassian.com/NEW", authorization_server_metadata=as_metadata)


# ----------------------------------------------------------------------
# Fakes
# ----------------------------------------------------------------------


class FakeServerCollection:
    """The subset of the pymongo mcpservers collection the service uses for registryOAuth."""

    def __init__(self) -> None:
        self.docs: dict[Any, dict[str, Any]] = {}
        self.before_update: list[Callable[[], None]] = []
        self.update_calls = 0

    @staticmethod
    def _path(doc: dict[str, Any], dotted: str) -> Any:
        value: Any = doc
        for part in dotted.split("."):
            if not isinstance(value, dict) or part not in value:
                return None
            value = value[part]
        return value

    def _matches(self, doc: dict[str, Any], query: dict[str, Any]) -> bool:
        for key, expected in query.items():
            if key == "registryOAuth" and expected == {"$exists": False}:
                if "registryOAuth" in doc:
                    return False
            elif self._path(doc, key) != expected:
                return False
        return True

    def apply_set(self, doc: dict[str, Any], updates: dict[str, Any]) -> None:
        for key, value in updates.items():
            if key == "registryOAuth":
                doc["registryOAuth"] = copy.deepcopy(value)
            else:
                _, field = key.split(".", 1)
                doc["registryOAuth"][field] = copy.deepcopy(value)

    async def update_one(self, query: dict[str, Any], update: dict[str, Any]) -> SimpleNamespace:
        self.update_calls += 1
        if self.before_update:
            self.before_update.pop(0)()
        doc = self.docs.setdefault(query["_id"], {"_id": query["_id"]})
        if not self._matches(doc, query):
            return SimpleNamespace(matched_count=0)
        self.apply_set(doc, update["$set"])
        return SimpleNamespace(matched_count=1)

    async def find_one(self, query: dict[str, Any], projection: dict[str, Any] | None = None) -> dict[str, Any] | None:
        doc = self.docs.get(query["_id"])
        return copy.deepcopy(doc) if doc is not None else None

    def state(self, server_id: Any = SERVER_ID) -> RegistryOAuthState | None:
        raw = self.docs.get(server_id, {}).get("registryOAuth")
        return RegistryOAuthState.model_validate(raw) if raw else None

    def put_state(self, state: RegistryOAuthState, server_id: Any = SERVER_ID) -> None:
        self.docs.setdefault(server_id, {"_id": server_id})["registryOAuth"] = state.model_dump()


class FakeTokenService:
    """In-memory stand-in for TokenService: one access and one refresh record per (user, server)."""

    def __init__(self) -> None:
        self.access: dict[tuple[str, str], SimpleNamespace] = {}
        self.refresh: dict[tuple[str, str], SimpleNamespace] = {}
        self.store_calls: list[dict[str, Any]] = []

    def put(
        self,
        server_name: str,
        *,
        access: str | None = None,
        refresh: str | None = None,
        metadata: dict[str, Any] | None = None,
        access_expired: bool = False,
    ) -> None:
        if access is not None:
            expires = datetime.now(UTC) + (timedelta(seconds=-10) if access_expired else timedelta(hours=1))
            self.access[(USER, server_name)] = SimpleNamespace(
                token=access, metadata=dict(metadata or {}), expiresAt=expires
            )
        if refresh is not None:
            self.refresh[(USER, server_name)] = SimpleNamespace(token=refresh, metadata=dict(metadata or {}))

    def _copy(self, record: SimpleNamespace | None) -> SimpleNamespace | None:
        return copy.deepcopy(record) if record is not None else None

    async def get_oauth_access_token(self, user_id: str, service_name: str) -> SimpleNamespace | None:
        record = self.access.get((user_id, service_name))
        if record is None or record.expiresAt <= datetime.now(UTC):
            return None
        return self._copy(record)

    async def get_oauth_refresh_token(self, user_id: str, service_name: str) -> SimpleNamespace | None:
        return self._copy(self.refresh.get((user_id, service_name)))

    async def has_refresh_token(self, user_id: str, service_name: str) -> bool:
        return (user_id, service_name) in self.refresh

    async def store_oauth_tokens(
        self, user_id: str, service_name: str, tokens: OAuthTokens, metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.store_calls.append({"user_id": user_id, "tokens": tokens, "metadata": dict(metadata or {})})
        expires = datetime.now(UTC) + timedelta(seconds=tokens.expires_in or 3600)
        self.access[(user_id, service_name)] = SimpleNamespace(
            token=tokens.access_token, metadata=dict(metadata or {}), expiresAt=expires
        )
        if tokens.refresh_token:
            self.refresh[(user_id, service_name)] = SimpleNamespace(
                token=tokens.refresh_token, metadata=dict(metadata or {})
            )
        return {}

    async def delete_access_token_if_matches(self, user_id: str, service_name: str, access_token: str) -> bool:
        record = self.access.get((user_id, service_name))
        if record is not None and record.token == access_token:
            del self.access[(user_id, service_name)]
            return True
        return False

    async def delete_oauth_tokens(self, user_id: str, service_name: str) -> bool:
        deleted = self.access.pop((user_id, service_name), None) or self.refresh.pop((user_id, service_name), None)
        self.refresh.pop((user_id, service_name), None)
        return bool(deleted)

    async def get_access_token_status(self, user_id: str, service_name: str) -> tuple[Any, bool]:
        record = self.access.get((user_id, service_name))
        if record is None:
            return None, False
        return self._copy(record), record.expiresAt > datetime.now(UTC)

    async def get_refresh_token_status(self, user_id: str, service_name: str) -> tuple[Any, bool]:
        record = self.refresh.get((user_id, service_name))
        return (self._copy(record), True) if record is not None else (None, False)


class FakeRedis:
    def __init__(self, *, fail_set: bool = False) -> None:
        self.fail_set = fail_set
        self.store: dict[str, str] = {}

    def set(self, key: str, value: str, *, nx: bool = False, px: int | None = None) -> bool | None:
        if self.fail_set:
            raise RedisConnectionError("redis down")
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    def eval(self, script: str, numkeys: int, key: str, token: str) -> int:
        if self.store.get(key) == token:
            del self.store[key]
            return 1
        return 0


# ----------------------------------------------------------------------
# Harness
# ----------------------------------------------------------------------


class Harness:
    def __init__(self, collection: FakeServerCollection, discover: AsyncMock) -> None:
        self.collection = collection
        self.discover = discover
        self.tokens = FakeTokenService()
        self.flow_manager = FlowStateManager(None, redis_key_prefix="test")
        self.service = MCPOAuthService(
            flow_manager=self.flow_manager,
            token_service_instance=self.tokens,  # type: ignore[arg-type]
            registry_app_name="jarvis-registry",
            base_redirect_url=BASE_URL,
            encryption_key=KEY,
            redis_client=None,
            redis_key_prefix="test",
        )
        self.registered = 0
        self.register = AsyncMock(side_effect=self._register)
        self.service.oauth_client.register_client = self.register
        self.refresh = AsyncMock(side_effect=self._refresh)
        self.service.oauth_client.refresh_tokens = self.refresh
        self.registration_echo_scope: str | None = "echoed by provider"

    async def _register(self, **kwargs: Any) -> OAuthClientInformation:
        self.registered += 1
        return OAuthClientInformation(
            client_id=f"client-{self.registered}",
            client_secret=f"secret-{self.registered}",
            scope=self.registration_echo_scope,
            token_endpoint_auth_method="client_secret_post",
        )

    async def _refresh(self, oauth_config: dict[str, Any], refresh_token: str, *, resource: str | None = None):
        return OAuthTokens(
            access_token=f"refreshed-from-{refresh_token}", refresh_token=f"{refresh_token}-r", expires_in=3600
        )

    def server(self, *, url: str = ATLASSIAN_URL, path: str = "/atlassian", oauth: dict[str, Any] | None = None):
        config: dict[str, Any] = {"url": url, "requiresOAuth": True}
        if oauth is not None:
            config["oauth"] = oauth
        return SimpleNamespace(
            id=SERVER_ID,
            serverName="atlassian",
            path=path,
            config=config,
            registryOAuth=self.collection.state(),
        )

    def redirect_uri(self, path: str = "/atlassian") -> str:
        return get_default_redirect_uri(path=path, base_url=BASE_URL)

    async def bound_dcr_state(self, discovery: DiscoveryResult | None = None) -> RegistryOAuthState:
        """Persist a state with one registered client by running a login-mode ensure."""
        self.discover.return_value = discovery or atlassian_discovery()
        return await self.service.ensure_registry_oauth_state(self.server(), force_discovery=True)

    def dcr_binding(self, state: RegistryOAuthState) -> dict[str, Any]:
        assert state.client is not None
        return {"clientId": state.client.clientId, "issuer": state.issuer, "resource": state.resource}


@pytest.fixture
def collection(monkeypatch: pytest.MonkeyPatch) -> FakeServerCollection:
    fake = FakeServerCollection()
    monkeypatch.setattr(ExtendedMCPServer, "get_pymongo_collection", classmethod(lambda cls: fake))
    return fake


@pytest.fixture
def discover() -> Iterator[AsyncMock]:
    mock = AsyncMock(return_value=atlassian_discovery())
    with patch("registry_pkgs.oauth.oauth_service.discover_mcp_oauth", mock):
        yield mock


@pytest.fixture
def h(collection: FakeServerCollection, discover: AsyncMock) -> Harness:
    return Harness(collection, discover)


@pytest.fixture(autouse=True)
def _reset_memory_locks() -> None:
    refresh_lock_module._memory_locks.clear()


# ----------------------------------------------------------------------
# ensure_registry_oauth_state
# ----------------------------------------------------------------------


class TestEnsureState:
    @pytest.mark.asyncio
    async def test_first_login_registers_and_persists_v2_client(self, h: Harness) -> None:
        server = h.server()
        config_before = copy.deepcopy(server.config)

        state = await h.service.ensure_registry_oauth_state(server, force_discovery=True)

        stored = h.collection.state()
        assert stored is not None and stored.client is not None
        assert stored.issuer == ATLASSIAN_ISSUER
        assert stored.client.clientId == "client-1"
        assert stored.client.clientSecret != "secret-1"
        assert decrypt_value(stored.client.clientSecret, encryption_key=KEY) == "secret-1"
        assert stored.client.redirectUri == h.redirect_uri()
        assert stored.serverUrl == ATLASSIAN_URL
        assert server.registryOAuth == state == stored
        assert server.config == config_before
        # The registration sends the discovery's scope, and the client keeps it (not the echoed one).
        assert h.register.await_args.kwargs["scope"] == atlassian_discovery().scope
        assert stored.client.scope == atlassian_discovery().scope

    @pytest.mark.asyncio
    async def test_bound_matching_state_reuses_client(self, h: Harness) -> None:
        first = await h.bound_dcr_state()
        second = await h.service.ensure_registry_oauth_state(h.server(), force_discovery=True)

        assert h.register.await_count == 1
        assert second.client is not None and first.client is not None
        assert second.client.clientId == first.client.clientId

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "variant",
        ["challenge_scope", "reordered_scopes"],
    )
    async def test_scope_noise_does_not_reregister(self, h: Harness, variant: str) -> None:
        if variant == "challenge_scope":
            discovery = atlassian_discovery(scope="read:me offline_access")
            await h.bound_dcr_state(discovery)
            h.discover.return_value = discovery
        else:
            await h.bound_dcr_state()
            reordered = " ".join(reversed(atlassian_discovery().scope.split()))
            h.discover.return_value = atlassian_discovery(scope=reordered)

        await h.service.ensure_registry_oauth_state(h.server(), force_discovery=True)

        assert h.register.await_count == 1

    @pytest.mark.asyncio
    async def test_stale_server_url_reregisters(self, h: Harness) -> None:
        state = await h.bound_dcr_state()
        # config.url edited in Chat: the stored binding is stale.
        h.collection.put_state(state.model_copy(update={"serverUrl": "https://mcp.atlassian.com/v1/sse"}))

        new_state = await h.service.ensure_registry_oauth_state(h.server(), force_discovery=True)

        assert h.register.await_count == 2
        assert new_state.client is not None and new_state.client.clientId == "client-2"

    @pytest.mark.asyncio
    async def test_changed_issuer_reregisters(self, h: Harness) -> None:
        await h.bound_dcr_state()
        h.discover.return_value = changed_discovery()

        state = await h.service.ensure_registry_oauth_state(h.server(), force_discovery=True)

        assert h.register.await_count == 2
        assert state.issuer == "https://auth.atlassian.com/NEW"

    @pytest.mark.asyncio
    async def test_changed_path_reregisters_with_new_redirect(self, h: Harness) -> None:
        await h.bound_dcr_state()

        state = await h.service.ensure_registry_oauth_state(h.server(path="/jira"), force_discovery=True)

        assert h.register.await_count == 2
        assert state.client is not None and state.client.redirectUri == h.redirect_uri("/jira")
        assert h.register.await_args.kwargs["redirect_uri"] == h.redirect_uri("/jira")

    @pytest.mark.asyncio
    async def test_expired_client_or_changed_scope_reregisters(self, h: Harness) -> None:
        state = await h.bound_dcr_state()
        assert state.client is not None
        expired = state.client.model_copy(update={"clientSecretExpiresAt": datetime.now(UTC) - timedelta(days=1)})
        h.collection.put_state(state.model_copy(update={"client": expired}))

        await h.service.ensure_registry_oauth_state(h.server(), force_discovery=True)
        assert h.register.await_count == 2

        h.discover.return_value = atlassian_discovery(scope="read:me")
        await h.service.ensure_registry_oauth_state(h.server(), force_discovery=True)
        assert h.register.await_count == 3

    @pytest.mark.asyncio
    async def test_refresh_mode_never_registers(self, h: Harness) -> None:
        state = await h.service.ensure_registry_oauth_state(h.server(), force_discovery=False)

        h.register.assert_not_awaited()
        assert state.client is None
        assert h.collection.state() == state  # a client-less state is persisted

    @pytest.mark.asyncio
    async def test_refresh_mode_with_usable_state_skips_discovery(self, h: Harness) -> None:
        await h.bound_dcr_state()
        h.discover.reset_mock()

        await h.service.ensure_registry_oauth_state(h.server(), force_discovery=False)

        h.discover.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_precomputed_discovery_is_used(self, h: Harness) -> None:
        state = await h.service.ensure_registry_oauth_state(
            h.server(), force_discovery=True, discovery=atlassian_discovery()
        )
        h.discover.assert_not_awaited()
        assert state.issuer == ATLASSIAN_ISSUER

    @pytest.mark.asyncio
    async def test_dcr_discovery_failure_propagates_and_persists_nothing(self, h: Harness) -> None:
        h.discover.side_effect = OAuthDiscoveryError("down")

        with pytest.raises(OAuthDiscoveryError):
            await h.service.ensure_registry_oauth_state(h.server(), force_discovery=True)

        assert h.collection.state() is None

    @pytest.mark.asyncio
    async def test_static_failed_discovery_persists_issuerless_state(self, h: Harness) -> None:
        static = {"client_id": "static-id", "authorization_url": "https://as/authorize", "token_url": "https://as/t"}
        h.discover.side_effect = OAuthDiscoveryError("no well-known")

        state = await h.service.ensure_registry_oauth_state(h.server(oauth=static), force_discovery=True)

        assert state.issuer is None and state.authorizationServerMetadata is None and state.client is None
        assert h.collection.state() == state

        # Later refreshes reuse it; only the next interactive login retries discovery.
        h.discover.reset_mock()
        await h.service.ensure_registry_oauth_state(h.server(oauth=static), force_discovery=False)
        h.discover.assert_not_awaited()
        await h.service.ensure_registry_oauth_state(h.server(oauth=static), force_discovery=True)
        h.discover.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_static_failed_discovery_keeps_good_state(self, h: Harness) -> None:
        static = {"client_id": "static-id"}
        good = await h.service.ensure_registry_oauth_state(h.server(oauth=static), force_discovery=True)
        h.discover.side_effect = OAuthDiscoveryError("transient")

        kept = await h.service.ensure_registry_oauth_state(h.server(oauth=static), force_discovery=True)

        assert kept == good
        assert h.collection.state() == good


class TestCompareAndSwap:
    @pytest.mark.asyncio
    async def test_concurrent_first_logins_converge_on_one_client(self, h: Harness) -> None:
        async def slow_register(**kwargs: Any) -> OAuthClientInformation:
            await asyncio.sleep(0)
            return await h._register(**kwargs)

        h.register.side_effect = slow_register
        server_a, server_b = h.server(), h.server()

        state_a, state_b = await asyncio.gather(
            h.service.ensure_registry_oauth_state(server_a, force_discovery=True),
            h.service.ensure_registry_oauth_state(server_b, force_discovery=True),
        )

        stored = h.collection.state()
        assert stored is not None and stored.client is not None
        assert state_a.client is not None and state_b.client is not None
        assert state_a.client.clientId == state_b.client.clientId == stored.client.clientId

    @pytest.mark.asyncio
    async def test_login_losing_to_clientless_refresh_write_uses_own_client(self, h: Harness) -> None:
        server = h.server()

        def refresh_mode_wins() -> None:
            clientless = MCPOAuthService._new_state(ATLASSIAN_URL, atlassian_discovery(), None)
            h.collection.put_state(clientless)

        h.collection.before_update.append(refresh_mode_wins)
        state = await h.service.ensure_registry_oauth_state(server, force_discovery=True)

        assert h.register.await_count == 1
        stored = h.collection.state()
        assert stored is not None and stored.client is not None
        assert stored.client.clientId == "client-1" == (state.client.clientId if state.client else None)

    @pytest.mark.asyncio
    async def test_reused_client_is_never_written_back_after_its_invalidation(self, h: Harness) -> None:
        original = await h.bound_dcr_state()
        assert original.client is not None
        server = h.server()

        def invalidation_wins() -> None:
            doc = h.collection.docs[SERVER_ID]
            h.collection.apply_set(doc, {"registryOAuth.client": None, "registryOAuth.revision": "invalidated"})

        h.collection.before_update.append(invalidation_wins)
        state = await h.service.ensure_registry_oauth_state(server, force_discovery=True)

        stored = h.collection.state()
        assert stored is not None and stored.client is not None
        assert stored.client.clientId == "client-2" != original.client.clientId
        assert state.client is not None and state.client.clientId == "client-2"

    @pytest.mark.asyncio
    async def test_attempt_limit_raises_and_initiate_returns_it(self, h: Harness) -> None:
        def someone_else_writes() -> None:
            h.collection.put_state(MCPOAuthService._new_state(ATLASSIAN_URL, atlassian_discovery(), None))

        h.collection.before_update.extend([someone_else_writes] * ENSURE_STATE_MAX_ATTEMPTS)
        with pytest.raises(RegistryOAuthStateConflictError):
            await h.service.ensure_registry_oauth_state(h.server(), force_discovery=True)

        h.collection.before_update.extend([someone_else_writes] * ENSURE_STATE_MAX_ATTEMPTS)
        flow_id, auth_url, error = await h.service.initiate_oauth_flow(USER, h.server())
        assert flow_id is None and auth_url is None
        assert error is not None and "Could not persist OAuth state" in error

    @pytest.mark.asyncio
    async def test_static_failed_discovery_losing_to_good_login_keeps_winner(self, h: Harness) -> None:
        static = {"client_id": "static-id"}
        h.discover.side_effect = OAuthDiscoveryError("down")
        winner = MCPOAuthService._new_state(ATLASSIAN_URL, atlassian_discovery(), None)
        h.collection.before_update.append(lambda: h.collection.put_state(winner))

        state = await h.service.ensure_registry_oauth_state(h.server(oauth=static), force_discovery=True)

        assert state == winner
        assert h.collection.state() == winner
        assert h.collection.update_calls == 1

    @pytest.mark.asyncio
    async def test_invalidate_only_clears_the_rejected_client(self, h: Harness) -> None:
        state = await h.bound_dcr_state()
        assert state.client is not None

        assert await h.service.invalidate_registry_oauth_client(h.server(), "some-other-client") is False
        assert h.collection.state() == state

        assert await h.service.invalidate_registry_oauth_client(h.server(), state.client.clientId) is True
        stored = h.collection.state()
        assert stored is not None and stored.client is None and stored.revision != state.revision


# ----------------------------------------------------------------------
# Login
# ----------------------------------------------------------------------


class TestLogin:
    @pytest.mark.asyncio
    async def test_authorization_url_carries_resource_and_discovered_scope(self, h: Harness) -> None:
        flow_id, auth_url, error = await h.service.initiate_oauth_flow(USER, h.server())

        assert error is None and auth_url is not None
        assert "resource=https%3A%2F%2Fmcp.atlassian.com%2Fv2%2Fmcp" in auth_url
        query = parse_qs(urlparse(auth_url).query)
        assert query["client_id"] == ["client-1"]
        assert set(query["scope"][0].split()) == set(atlassian_discovery().scope.split())
        assert auth_url.startswith("https://auth.atlassian.com/authorize")
        flow = h.flow_manager.get_flow(flow_id)
        assert flow is not None and flow.metadata is not None
        assert flow.metadata.resource_metadata is not None
        assert flow.metadata.resource_metadata.resource == ATLASSIAN_URL
        assert flow.metadata.client_info.client_secret == "secret-1"
        # The flow authenticates at the token endpoint the way the client was registered.
        assert flow.metadata.metadata.token_endpoint_auth_methods_supported == ["client_secret_post"]

    @pytest.mark.asyncio
    async def test_static_login_uses_static_client_and_scope(self, h: Harness) -> None:
        static = {"client_id": "static-id", "client_secret": "s", "scope": "repo"}
        flow_id, auth_url, error = await h.service.initiate_oauth_flow(USER, h.server(oauth=static))

        assert error is None and auth_url is not None
        query = parse_qs(urlparse(auth_url).query)
        assert query["client_id"] == ["static-id"] and query["scope"] == ["repo"]
        h.register.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dcr_without_registration_endpoint_fails(self, h: Harness) -> None:
        as_metadata = {k: v for k, v in _load("atlassian_v2_as_metadata.json").items() if k != "registration_endpoint"}
        h.discover.return_value = atlassian_discovery(authorization_server_metadata=as_metadata)

        flow_id, _, error = await h.service.initiate_oauth_flow(USER, h.server())

        assert flow_id is None
        assert error is not None and "requires client_id or dynamic client registration support" in error

    async def _start_and_complete(self, h: Harness, *, tokens: OAuthTokens | None = None, exchange_error=None):
        flow_id, _, error = await h.service.initiate_oauth_flow(USER, h.server())
        assert error is None and flow_id is not None
        flow = h.flow_manager.get_flow(flow_id)
        assert flow is not None
        if exchange_error is not None:
            h.service.oauth_client.exchange_code_for_tokens = AsyncMock(side_effect=exchange_error)
        else:
            h.service.oauth_client.exchange_code_for_tokens = AsyncMock(
                return_value=tokens or OAuthTokens(access_token="login-access", refresh_token="login-refresh")
            )
        return await h.service.complete_oauth_flow(flow_id, "code-1", flow.state)

    @pytest.mark.asyncio
    async def test_complete_stores_tokens_with_flow_binding(self, h: Harness) -> None:
        success, error = await self._start_and_complete(h)

        assert success and error is None
        record = h.tokens.refresh[(USER, "atlassian")]
        assert record.metadata == {"clientId": "client-1", "issuer": ATLASSIAN_ISSUER, "resource": ATLASSIAN_URL}
        assert h.tokens.access[(USER, "atlassian")].metadata == record.metadata

    @pytest.mark.asyncio
    async def test_invalid_client_at_code_exchange_clears_client(self, h: Harness) -> None:
        success, _ = await self._start_and_complete(
            h, exchange_error=OAuthTokenEndpointError("rejected", error_code="invalid_client")
        )

        assert not success
        stored = h.collection.state()
        assert stored is not None and stored.client is None
        await h.service.initiate_oauth_flow(USER, h.server())
        assert h.register.await_count == 2

    @pytest.mark.asyncio
    async def test_redis_error_on_lock_still_stores_login_tokens(self, h: Harness) -> None:
        h.service._lock_redis_client = FakeRedis(fail_set=True)  # type: ignore[assignment]

        success, _ = await self._start_and_complete(h)

        assert success
        assert h.tokens.access[(USER, "atlassian")].token == "login-access"


# ----------------------------------------------------------------------
# Refresh and use-time binding
# ----------------------------------------------------------------------


class TestRefresh:
    @pytest.mark.asyncio
    async def test_dcr_expired_access_refreshes_without_login(self, h: Harness) -> None:
        state = await h.bound_dcr_state()
        h.tokens.put("atlassian", access="old", refresh="r1", metadata=h.dcr_binding(state), access_expired=True)
        h.service.initiate_oauth_flow = AsyncMock()  # type: ignore[method-assign]

        token, auth_url, error = await h.service.get_valid_access_token(USER, h.server())

        assert (token, auth_url, error) == ("refreshed-from-r1", None, None)
        h.service.initiate_oauth_flow.assert_not_awaited()
        config, refresh_token = h.refresh.await_args.args
        assert refresh_token == "r1"
        assert h.refresh.await_args.kwargs == {"resource": ATLASSIAN_URL}
        assert config["client_id"] == "client-1" and config["client_secret"] == "secret-1"
        assert config["token_url"] == "https://auth.atlassian.com/oauth/token"
        assert h.tokens.store_calls[-1]["metadata"] == h.dcr_binding(state)

    @pytest.mark.asyncio
    async def test_refresh_token_of_another_client_leads_to_login(self, h: Harness) -> None:
        state = await h.bound_dcr_state()
        binding = {**h.dcr_binding(state), "clientId": "old-client"}
        h.tokens.put("atlassian", refresh="r1", metadata=binding)

        success, error = await h.service.validate_and_refresh_tokens(USER, h.server())

        assert not success and error is not None and "bound to a different" in error
        h.refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dcr_access_token_bound_to_old_as_is_not_returned(self, h: Harness) -> None:
        state = await h.bound_dcr_state()
        old = {**h.dcr_binding(state), "issuer": "https://auth.atlassian.com"}
        h.tokens.put("atlassian", access="v1-token", refresh="r1", metadata=old)
        h.service.initiate_oauth_flow = AsyncMock(return_value=("f", "https://login", None))  # type: ignore[method-assign]

        token, auth_url, _ = await h.service.get_valid_access_token(USER, h.server())

        assert token is None and auth_url == "https://login"
        h.refresh.assert_not_awaited()  # the refresh token fails the same binding check

    @pytest.mark.asyncio
    async def test_reregistered_client_keeps_other_users_access_token(self, h: Harness) -> None:
        state = await h.bound_dcr_state()
        old_binding = {**h.dcr_binding(state), "clientId": "previous-client"}
        h.tokens.put("atlassian", access="still-valid", refresh="r1", metadata=old_binding)

        token, _, _ = await h.service.get_valid_access_token(USER, h.server())
        assert token == "still-valid"

        h.tokens.access[(USER, "atlassian")].expiresAt = datetime.now(UTC) - timedelta(seconds=10)
        success, _ = await h.service.validate_and_refresh_tokens(USER, h.server())
        assert not success
        h.refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_use_time_discovery_error_returns_once(self, h: Harness) -> None:
        h.tokens.put("atlassian", access="a", refresh="r", metadata={"clientId": "c"})
        h.discover.side_effect = OAuthDiscoveryError("down")
        h.service.initiate_oauth_flow = AsyncMock()  # type: ignore[method-assign]

        token, auth_url, error = await h.service.get_valid_access_token(USER, h.server())

        assert token is None and auth_url is None and error is not None and "discovery failed" in error
        h.discover.assert_awaited_once()
        h.refresh.assert_not_awaited()
        h.service.initiate_oauth_flow.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_refresh_with_discovery_error_returns_false(self, h: Harness) -> None:
        h.discover.side_effect = OAuthDiscoveryError("down")
        assert await h.service._refresh_with_lock(USER, h.server()) == (False, "down")

    @pytest.mark.asyncio
    async def test_invalid_client_at_refresh_clears_only_that_client(self, h: Harness) -> None:
        state = await h.bound_dcr_state()
        h.tokens.put("atlassian", refresh="r1", metadata=h.dcr_binding(state))
        h.refresh.side_effect = OAuthTokenEndpointError("gone", error_code="invalid_client")

        success, _ = await h.service.validate_and_refresh_tokens(USER, h.server())

        assert not success
        stored = h.collection.state()
        assert stored is not None and stored.client is None
        # A refresh after the client was cleared never registers one.
        success, error = await h.service.validate_and_refresh_tokens(USER, h.server())
        assert not success and error is not None and "No OAuth client" in error
        assert h.register.await_count == 1

    @pytest.mark.asyncio
    async def test_invalid_client_for_old_client_leaves_newer_client(self, h: Harness) -> None:
        state = await h.bound_dcr_state()
        h.tokens.put("atlassian", refresh="r1", metadata=h.dcr_binding(state))

        async def newer_login_then_reject(*args: Any, **kwargs: Any) -> None:
            assert state.client is not None
            newer = state.client.model_copy(update={"clientId": "client-B"})
            h.collection.put_state(state.model_copy(update={"client": newer, "revision": "rev-B"}))
            raise OAuthTokenEndpointError("gone", error_code="invalid_client")

        h.refresh.side_effect = newer_login_then_reject
        await h.service.validate_and_refresh_tokens(USER, h.server())

        stored = h.collection.state()
        assert stored is not None and stored.client is not None and stored.client.clientId == "client-B"

    @pytest.mark.asyncio
    async def test_login_during_inflight_refresh_keeps_login_tokens(self, h: Harness) -> None:
        state = await h.bound_dcr_state()
        binding = h.dcr_binding(state)
        h.tokens.put("atlassian", refresh="r1", metadata=binding)

        async def slow_refresh(*args: Any, **kwargs: Any) -> OAuthTokens:
            # The lock expired mid-call and a login stored its tokens.
            await h.tokens.store_oauth_tokens(
                USER, "atlassian", OAuthTokens(access_token="login-access", refresh_token="login-r"), binding
            )
            return OAuthTokens(access_token="stale-refresh-access", refresh_token="r1-r")

        h.refresh.side_effect = slow_refresh
        success, _ = await h.service.validate_and_refresh_tokens(USER, h.server())

        assert success
        token, _, _ = await h.service.get_valid_access_token(USER, h.server())
        assert token == "login-access"
        assert h.tokens.refresh[(USER, "atlassian")].token == "login-r"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("rotates", [True, False])
    async def test_concurrent_refreshes_call_token_endpoint_once(self, h: Harness, rotates: bool) -> None:
        state = await h.bound_dcr_state()
        h.tokens.put("atlassian", refresh="r1", metadata=h.dcr_binding(state))

        async def slow_refresh(config: dict[str, Any], refresh_token: str, *, resource: str | None = None):
            await asyncio.sleep(0.02)
            return OAuthTokens(access_token="fresh", refresh_token="r2" if rotates else refresh_token)

        h.refresh.side_effect = slow_refresh
        results = await asyncio.gather(
            h.service.validate_and_refresh_tokens(USER, h.server()),
            h.service.validate_and_refresh_tokens(USER, h.server()),
        )

        assert results == [(True, None), (True, None)]
        assert h.refresh.await_count == 1
        assert h.tokens.access[(USER, "atlassian")].token == "fresh"

    @pytest.mark.asyncio
    async def test_lock_timeout(self, h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(refresh_lock_module, "REFRESH_LOCK_WAIT_SECONDS", 0.05)
        state = await h.bound_dcr_state()
        h.tokens.put("atlassian", refresh="r1", metadata=h.dcr_binding(state))
        held = asyncio.Event()
        release = asyncio.Event()

        async def holder() -> None:
            async with refresh_lock(None, key_prefix="test", user_id=USER, server_id=str(SERVER_ID)):
                held.set()
                await release.wait()

        task = asyncio.create_task(holder())
        await held.wait()
        assert await h.service.validate_and_refresh_tokens(USER, h.server()) == (False, "refresh already in progress")

        h.tokens.put("atlassian", access="fresh-from-other-pod", metadata=h.dcr_binding(state))
        assert await h.service.validate_and_refresh_tokens(USER, h.server()) == (True, None)
        release.set()
        await task
        h.refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_redis_error_on_lock_acquire_fails_refresh(self, h: Harness) -> None:
        state = await h.bound_dcr_state()
        h.tokens.put("atlassian", refresh="r1", metadata=h.dcr_binding(state))
        h.service._lock_redis_client = FakeRedis(fail_set=True)  # type: ignore[assignment]

        success, error = await h.service._refresh_with_lock(USER, h.server())

        assert not success and error is not None and "lock" in error
        h.refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_static_binding_is_client_id_only(self, h: Harness) -> None:
        static = {"client_id": "static-id", "token_url": "https://as/token", "issuer": "https://manual-issuer"}
        # Logged in while discovery failed: the token's issuer/resource are whatever the flow had.
        h.tokens.put("atlassian", refresh="r1", metadata={"clientId": "static-id", "issuer": None, "resource": None})
        # A later login's discovery succeeds and sets an issuer and resource.
        await h.service.ensure_registry_oauth_state(h.server(oauth=static), force_discovery=True)

        assert await h.service.validate_and_refresh_tokens(USER, h.server(oauth=static)) == (True, None)
        assert h.refresh.await_args.args[0]["token_url"] == "https://as/token"  # manual URL wins

        h.tokens.put("atlassian", refresh="r2", metadata={"clientId": "static-id"})
        changed = {**static, "client_id": "rotated-id"}
        success, _ = await h.service.validate_and_refresh_tokens(USER, h.server(oauth=changed))
        assert not success
        assert h.refresh.await_count == 1

    @pytest.mark.asyncio
    async def test_static_access_token_skips_use_time_check(self, h: Harness) -> None:
        h.tokens.put("atlassian", access="a", metadata={"clientId": "static-id", "issuer": "whatever"})

        token, _, _ = await h.service.get_valid_access_token(USER, h.server(oauth={"client_id": "static-id"}))

        assert token == "a"
        h.discover.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_interactive_raises_without_flow_or_registration(self, h: Harness) -> None:
        with pytest.raises(OAuthReAuthRequiredError) as exc_info:
            await h.service.get_valid_access_token(USER, h.server(), interactive=False)

        assert exc_info.value.auth_url is None
        assert h.flow_manager.get_user_flows(USER, str(SERVER_ID)) == []
        h.register.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reinitialize_refreshes_dcr_server(self, h: Harness) -> None:
        state = await h.bound_dcr_state()
        h.tokens.put("atlassian", access="old", refresh="r1", metadata=h.dcr_binding(state), access_expired=True)

        needs_connection, response = await h.service.handle_reinitialize_auth(USER, h.server())

        assert needs_connection and response["oauth_required"] is False
        h.refresh.assert_awaited_once()

    def test_refresh_token_method_removed(self) -> None:
        assert not hasattr(MCPOAuthService, "refresh_token")


# ----------------------------------------------------------------------
# Downstream 401 recovery
# ----------------------------------------------------------------------


class TestRecoverFromUnauthorized:
    @pytest.mark.asyncio
    async def test_first_401_refresh_success_returns_new_token(self, h: Harness) -> None:
        state = await h.bound_dcr_state()
        h.tokens.put("atlassian", access="rejected", refresh="r1", metadata=h.dcr_binding(state))

        token = await h.service.recover_from_unauthorized(
            USER, h.server(), rejected_access_token="rejected", www_authenticate=None, is_retry=False
        )

        assert token == "refreshed-from-r1"

    @pytest.mark.asyncio
    async def test_first_401_refresh_failure_deletes_rejected_token(self, h: Harness) -> None:
        h.tokens.put("atlassian", access="rejected", refresh="r1", metadata={"clientId": "gone"})
        await h.bound_dcr_state()

        token = await h.service.recover_from_unauthorized(
            USER, h.server(), rejected_access_token="rejected", www_authenticate=None, is_retry=False
        )

        assert token is None
        assert (USER, "atlassian") not in h.tokens.access

    @pytest.mark.asyncio
    async def test_second_401_unchanged_discovery_is_loop_guard(self, h: Harness) -> None:
        await h.bound_dcr_state()
        h.tokens.put("atlassian", access="fresh")

        with pytest.raises(DownstreamAuthRejectedException, match="rejected a freshly issued access token"):
            await h.service.recover_from_unauthorized(
                USER, h.server(), rejected_access_token="fresh", www_authenticate="Bearer", is_retry=True
            )

        h.refresh.assert_not_awaited()
        assert h.flow_manager.get_user_flows(USER, str(SERVER_ID)) == []
        assert h.discover.await_args.kwargs["www_authenticate"] == "Bearer"

    @pytest.mark.asyncio
    async def test_second_401_changed_discovery_persists_and_clears_tokens(self, h: Harness) -> None:
        await h.bound_dcr_state()
        h.tokens.put("atlassian", access="fresh", refresh="r1")
        h.discover.return_value = changed_discovery()

        token = await h.service.recover_from_unauthorized(
            USER, h.server(), rejected_access_token="fresh", www_authenticate=None, is_retry=True
        )

        assert token is None
        stored = h.collection.state()
        assert stored is not None and stored.issuer == "https://auth.atlassian.com/NEW"
        assert stored.client is not None and stored.client.clientId == "client-2"
        assert (USER, "atlassian") not in h.tokens.access and (USER, "atlassian") not in h.tokens.refresh

    @pytest.mark.asyncio
    async def test_second_401_discovery_failure_is_loop_guard(self, h: Harness) -> None:
        await h.bound_dcr_state()
        h.discover.side_effect = OAuthDiscoveryError("down")

        with pytest.raises(DownstreamAuthRejectedException):
            await h.service.recover_from_unauthorized(
                USER, h.server(), rejected_access_token="fresh", www_authenticate=None, is_retry=True
            )

    @pytest.mark.asyncio
    async def test_static_second_401_raises_without_discovery_or_deletion(self, h: Harness) -> None:
        h.tokens.put("atlassian", access="fresh", refresh="r1")

        with pytest.raises(DownstreamAuthRejectedException):
            await h.service.recover_from_unauthorized(
                USER,
                h.server(oauth={"client_id": "static-id"}),
                rejected_access_token="fresh",
                www_authenticate=None,
                is_retry=True,
            )

        h.discover.assert_not_awaited()
        assert h.tokens.access[(USER, "atlassian")].token == "fresh"


# ----------------------------------------------------------------------
# Flow bookkeeping and reinitialize (mock-based)
# ----------------------------------------------------------------------


class TestMCPOAuthService:
    """Unit tests for MCPOAuthService paths that don't touch registryOAuth"""

    def test_oauth_flow_status_enum_contains_only_reachable_states(self) -> None:
        assert {status.value for status in OAuthFlowStatus} == {"pending", "completed", "failed"}

    @pytest.fixture
    def mock_flow_manager(self):
        return Mock(spec=FlowStateManager)

    @pytest.fixture
    def oauth_service(self, mock_flow_manager):
        service = MCPOAuthService(
            flow_manager=mock_flow_manager,
            token_service_instance=Mock(spec=TokenService),
            registry_app_name="jarvis-registry",
            base_redirect_url=BASE_URL,
            encryption_key=KEY,
            redis_client=None,
            redis_key_prefix="test",
        )
        service.oauth_client = Mock()
        return service

    @pytest.fixture
    def mock_server(self):
        server = Mock(spec=ExtendedMCPServer)
        server.id = SERVER_ID
        server.serverName = "test_server"
        server.path = "/test_server"
        server.config = {"oauth": {"client_id": "test_client_id"}, "requiresOAuth": True}
        return server

    def _mock_token_service(self, token_service_mock, *, access_exists, access_valid, refresh_exists, refresh_valid):
        access_doc = Mock(token="expired_access_token") if access_exists else None
        refresh_doc = Mock(token="valid_refresh_token") if refresh_exists else None
        token_service_mock.get_access_token_status = AsyncMock(return_value=(access_doc, access_valid))
        token_service_mock.get_refresh_token_status = AsyncMock(return_value=(refresh_doc, refresh_valid))

    def test_redis_lock_only_when_flow_manager_uses_redis(self) -> None:
        redis = Mock()
        memory_manager = Mock(spec=FlowStateManager, uses_redis=False)
        redis_manager = Mock(spec=FlowStateManager, uses_redis=True)
        kwargs = {
            "token_service_instance": Mock(),
            "registry_app_name": "r",
            "base_redirect_url": BASE_URL,
            "encryption_key": KEY,
            "redis_client": redis,
            "redis_key_prefix": "p",
        }
        assert MCPOAuthService(flow_manager=memory_manager, **kwargs)._lock_redis_client is None
        assert MCPOAuthService(flow_manager=redis_manager, **kwargs)._lock_redis_client is redis

    @pytest.mark.asyncio
    async def test_handle_reinitialize_auth_access_token_valid(self, oauth_service, mock_server):
        self._mock_token_service(
            oauth_service.token_service, access_exists=True, access_valid=True, refresh_exists=True, refresh_valid=True
        )

        needs_connection, response_data = await oauth_service.handle_reinitialize_auth(USER, mock_server)

        assert needs_connection
        assert response_data["success"]
        assert response_data["server_name"] == "test_server"
        assert "reinitialized successfully" in response_data["message"]
        assert response_data["oauth_required"] is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize("access_exists", [True, False])
    async def test_handle_reinitialize_auth_refresh_valid_refreshes(self, oauth_service, mock_server, access_exists):
        self._mock_token_service(
            oauth_service.token_service,
            access_exists=access_exists,
            access_valid=False,
            refresh_exists=True,
            refresh_valid=True,
        )
        with patch.object(
            oauth_service,
            "_refresh_and_connect",
            AsyncMock(return_value=(True, {"success": True, "message": "Refreshed successfully"})),
        ):
            needs_connection, response_data = await oauth_service.handle_reinitialize_auth(USER, mock_server)

        assert needs_connection
        assert response_data["success"]

    @pytest.mark.asyncio
    async def test_handle_reinitialize_auth_no_valid_tokens(self, oauth_service, mock_server):
        self._mock_token_service(
            oauth_service.token_service,
            access_exists=False,
            access_valid=False,
            refresh_exists=False,
            refresh_valid=False,
        )

        needs_connection, response_data = await oauth_service.handle_reinitialize_auth(USER, mock_server)

        assert not needs_connection
        assert response_data["success"]
        assert "OAuth authorization required" in response_data["message"]
        assert response_data["oauth_required"] is True

    @pytest.mark.asyncio
    async def test_refresh_and_connect_failure_requires_oauth(self, oauth_service, mock_server):
        with patch.object(oauth_service, "_refresh_with_lock", AsyncMock(return_value=(False, "nope"))):
            needs_connection, response_data = await oauth_service._refresh_and_connect(USER, mock_server)

        assert not needs_connection
        assert response_data["oauth_required"] is True

    @pytest.mark.asyncio
    async def test_complete_failed_flow_rejects_retry_past_original_ttl(self, oauth_service) -> None:
        flow_id = "test_flow_id"
        state = "test_flow_id##security_token"
        flow = Mock(state=state, status=OAuthFlowStatus.FAILED)
        oauth_service.flow_manager.decode_state.return_value = {"flow_id": flow_id, "security_token": "security_token"}
        oauth_service.flow_manager.get_flow.return_value = flow
        oauth_service.flow_manager.is_flow_expired.return_value = True
        oauth_service.oauth_client.exchange_code_for_tokens = AsyncMock()

        success, error = await oauth_service.complete_oauth_flow(flow_id, "test_code", state)

        assert success is False
        assert error == "Flow expired"
        oauth_service.flow_manager.delete_flow.assert_called_once_with(flow_id)
        oauth_service.oauth_client.exchange_code_for_tokens.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_complete_oauth_flow_invalid_state(self, oauth_service):
        oauth_service.flow_manager.decode_state = Mock(side_effect=ValueError("Invalid state"))
        success, error = await oauth_service.complete_oauth_flow("test_flow_id", "test_code", "invalid_state")

        assert not success
        assert "Invalid state format" in error

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("flow_status", "completed", "failed"),
        [
            (OAuthFlowStatus.PENDING, False, False),
            (OAuthFlowStatus.FAILED, False, True),
            (OAuthFlowStatus.COMPLETED, True, False),
        ],
    )
    async def test_get_flow_status_returns_uniform_found_shape(
        self,
        oauth_service,
        flow_status: OAuthFlowStatus,
        completed: bool,
        failed: bool,
    ) -> None:
        flow = Mock(
            status=flow_status,
            error="denied" if failed else None,
            server_id="server-1",
            user_id="user-1",
            created_at=100.0,
            completed_at=120.0 if completed else None,
        )
        oauth_service.flow_manager.get_flow.return_value = flow
        oauth_service.flow_manager.is_flow_expired.return_value = False

        result = await oauth_service.get_flow_status("user-1:server-1")

        assert isinstance(result, OAuthFlowStatusResponse)
        assert result.status == flow_status
        assert result.completed is completed
        assert result.failed is failed
        assert result.server_id == "server-1"
        assert result.user_id == "user-1"
        assert result.created_at == 100.0
        assert result.completed_at == (120.0 if completed else None)
        oauth_service.flow_manager.delete_flow.assert_not_called()
        if completed:
            oauth_service.flow_manager.is_flow_expired.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_flow_status_returns_uniform_not_found_shape(self, oauth_service) -> None:
        oauth_service.flow_manager.get_flow.return_value = None

        result = await oauth_service.get_flow_status("user-1:server-1")

        assert result == OAuthFlowStatusResponse(status="not_found", completed=False, failed=False)
        oauth_service.flow_manager.is_flow_expired.assert_not_called()
        oauth_service.flow_manager.delete_flow.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("flow_status", [OAuthFlowStatus.PENDING, OAuthFlowStatus.FAILED])
    async def test_get_flow_status_lazily_deletes_logically_expired_unfinished_flow(
        self,
        oauth_service,
        flow_status: OAuthFlowStatus,
    ) -> None:
        flow = Mock(status=flow_status)
        oauth_service.flow_manager.get_flow.return_value = flow
        oauth_service.flow_manager.is_flow_expired.return_value = True

        result = await oauth_service.get_flow_status("user-1:server-1")

        assert result == OAuthFlowStatusResponse(status="not_found", completed=False, failed=False)
        oauth_service.flow_manager.is_flow_expired.assert_called_once_with(flow)
        oauth_service.flow_manager.delete_flow.assert_called_once_with("user-1:server-1")

    @pytest.mark.asyncio
    async def test_get_tokens_success(self, oauth_service):
        mock_tokens = Mock(spec=OAuthTokens)
        oauth_service.token_service.get_oauth_tokens = AsyncMock(return_value=mock_tokens)

        result = await oauth_service.get_tokens(USER, "test_server")

        assert result == mock_tokens
        oauth_service.token_service.get_oauth_tokens.assert_awaited_once_with(USER, "test_server")

    @pytest.mark.asyncio
    async def test_cancel_oauth_flow_success(self, oauth_service):
        oauth_service.flow_manager.cancel_user_flow = Mock(return_value=True)
        success, error = await oauth_service.cancel_oauth_flow(USER, str(SERVER_ID))

        assert success
        assert error is None

    @pytest.mark.asyncio
    async def test_has_active_flow_true(self, oauth_service):
        oauth_service.flow_manager.get_user_flows = Mock(return_value=[Mock()])
        assert await oauth_service.has_active_flow(USER, "test_server")

    @pytest.mark.asyncio
    async def test_has_failed_flow_true(self, oauth_service):
        oauth_service.flow_manager.get_user_flows = Mock(return_value=[Mock(status=OAuthFlowStatus.FAILED)])
        assert await oauth_service.has_failed_flow(USER, "test_server")


def test_registry_oauth_client_model_round_trip() -> None:
    client = RegistryOAuthClient(
        clientId="c", redirectUri="r", tokenEndpointAuthMethod="none", registeredAt=datetime.now(UTC)
    )
    assert RegistryOAuthClient.model_validate(client.model_dump()) == client
