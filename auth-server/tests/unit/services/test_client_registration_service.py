"""Unit tests for dynamic client registration business rules."""

import pytest
from tests.support.oauth_state_store import InMemoryOAuthStateStore

from auth_server.models.client_registration import ClientRegistrationRequest
from auth_server.services.client_registration_service import ClientRegistrationError, ClientRegistrationService
from registry_pkgs.core.client_categories import (
    AUTHORIZATION_CODE_GRANT_TYPE,
    REFRESH_TOKEN_GRANT_TYPE,
    ClientCategory,
)
from registry_pkgs.core.downstream_oauth import DEVICE_CODE_GRANT_TYPE


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


def test_register_persists_requested_grant_types(
    service: ClientRegistrationService,
    store: InMemoryOAuthStateStore,
) -> None:
    response = service.register(
        ClientRegistrationRequest(
            redirect_uris=["https://example.com/callback"],
            grant_types=[AUTHORIZATION_CODE_GRANT_TYPE],
        ),
        category=ClientCategory.MCP_DCR,
        default_client_name="MCP Client",
        ip_address="127.0.0.1",
    )

    assert response.grant_types == [AUTHORIZATION_CODE_GRANT_TYPE]
    assert store.registered_clients[response.client_id]["grant_types"] == [AUTHORIZATION_CODE_GRANT_TYPE]


def test_register_defaults_to_policy_grant_types_when_omitted(
    service: ClientRegistrationService,
    store: InMemoryOAuthStateStore,
) -> None:
    response = service.register(
        ClientRegistrationRequest(redirect_uris=["https://example.com/callback"]),
        category=ClientCategory.MCP_DCR,
        default_client_name="MCP Client",
        ip_address="127.0.0.1",
    )

    expected = [AUTHORIZATION_CODE_GRANT_TYPE, REFRESH_TOKEN_GRANT_TYPE, DEVICE_CODE_GRANT_TYPE]
    assert response.grant_types == expected
    assert store.registered_clients[response.client_id]["grant_types"] == expected


def test_register_orders_and_deduplicates_supported_grant_types(
    service: ClientRegistrationService,
    store: InMemoryOAuthStateStore,
) -> None:
    response = service.register(
        ClientRegistrationRequest(
            redirect_uris=["https://example.com/callback"],
            grant_types=[
                DEVICE_CODE_GRANT_TYPE,
                "client_credentials",
                AUTHORIZATION_CODE_GRANT_TYPE,
                DEVICE_CODE_GRANT_TYPE,
            ],
        ),
        category=ClientCategory.MCP_DCR,
        default_client_name="MCP Client",
        ip_address="127.0.0.1",
    )

    expected = [AUTHORIZATION_CODE_GRANT_TYPE, DEVICE_CODE_GRANT_TYPE]
    assert response.grant_types == expected
    assert store.registered_clients[response.client_id]["grant_types"] == expected


@pytest.mark.parametrize("grant_types", [[], ["client_credentials"]])
def test_register_rejects_empty_grant_type_intersection_before_persisting(
    service: ClientRegistrationService,
    store: InMemoryOAuthStateStore,
    grant_types: list[str],
) -> None:
    with pytest.raises(ClientRegistrationError) as exc_info:
        service.register(
            ClientRegistrationRequest(
                redirect_uris=["https://example.com/callback"],
                grant_types=grant_types,
            ),
            category=ClientCategory.MCP_DCR,
            default_client_name="MCP Client",
            ip_address="127.0.0.1",
        )

    assert exc_info.value.error == "invalid_client_metadata"
    assert exc_info.value.description == "none of the requested grant_types are supported"
    assert store.registered_clients == {}


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
