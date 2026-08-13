"""Unit tests for dynamic client registration business rules."""

import pytest
from tests.support.oauth_state_store import InMemoryOAuthStateStore

from auth_server.models.client_registration import ClientRegistrationRequest
from auth_server.services.client_registration_service import ClientRegistrationError, ClientRegistrationService
from registry_pkgs.core.client_categories import ClientCategory


@pytest.fixture
def store() -> InMemoryOAuthStateStore:
    return InMemoryOAuthStateStore()


@pytest.fixture
def service(store: InMemoryOAuthStateStore) -> ClientRegistrationService:
    return ClientRegistrationService(store)


def test_register_preserves_requested_scope(
    service: ClientRegistrationService,
    store: InMemoryOAuthStateStore,
) -> None:
    response = service.register(
        ClientRegistrationRequest(
            redirect_uris=["https://example.com/callback"],
            scope="  mcp-proxy-ops   mcp-proxy-ops  ",
        ),
        category=ClientCategory.MCP_DCR,
        default_client_name="MCP Client",
        ip_address="127.0.0.1",
    )

    assert response.scope == "  mcp-proxy-ops   mcp-proxy-ops  "
    assert store.registered_clients[response.client_id]["scope"] == response.scope


def test_register_preserves_whitespace_only_scope(
    service: ClientRegistrationService,
    store: InMemoryOAuthStateStore,
) -> None:
    response = service.register(
        ClientRegistrationRequest(
            redirect_uris=["https://example.com/callback"],
            scope="   ",
        ),
        category=ClientCategory.MCP_DCR,
        default_client_name="MCP Client",
        ip_address="127.0.0.1",
    )

    assert response.scope == "   "
    assert store.registered_clients[response.client_id]["scope"] == "   "


def test_register_rejects_unsafe_redirect_before_persisting(
    service: ClientRegistrationService,
    store: InMemoryOAuthStateStore,
) -> None:
    with pytest.raises(ClientRegistrationError) as exc_info:
        service.register(
            ClientRegistrationRequest(redirect_uris=["javascript:alert(1)"]),
            category=ClientCategory.MCP_DCR,
            default_client_name="MCP Client",
            ip_address="127.0.0.1",
        )

    assert exc_info.value.error == "invalid_redirect_uri"
    assert store.registered_clients == {}


def test_register_propagates_store_failure(
    service: ClientRegistrationService,
    store: InMemoryOAuthStateStore,
) -> None:
    original_error = RuntimeError("store unavailable")

    def fail_save(client_id: str, metadata: dict) -> None:
        raise original_error

    store.save_client = fail_save

    with pytest.raises(RuntimeError) as exc_info:
        service.register(
            ClientRegistrationRequest(redirect_uris=["https://example.com/callback"]),
            category=ClientCategory.MCP_DCR,
            default_client_name="MCP Client",
            ip_address="127.0.0.1",
        )

    assert exc_info.value is original_error
