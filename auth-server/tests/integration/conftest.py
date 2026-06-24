"""
Shared test utilities for auth_server integration tests.
"""

from unittest.mock import AsyncMock, MagicMock


def _mock_keycloak_provider() -> MagicMock:
    """Return a fully-wired MagicMock suitable for use as a Keycloak auth provider override.

    Configures get_jwks as an AsyncMock so that _decode_oidc_provider_token can
    await it without raising TypeError, and sets the realm/client attributes that
    _provider_token_issuers and _provider_token_audience inspect.
    """
    provider = MagicMock()
    provider.get_jwks = AsyncMock(return_value={"keys": [{"kid": "test-kid"}]})
    provider.client_id = "test-client"
    provider.m2m_client_id = "test-client"
    provider.realm = "test-realm"
    provider.realm_url = "http://localhost:8888/realms/test-realm"
    provider.external_realm_url = "http://localhost:8888/realms/test-realm"
    return provider
