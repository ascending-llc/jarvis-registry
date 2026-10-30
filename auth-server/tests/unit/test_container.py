"""Unit tests for AuthContainer."""

from unittest.mock import MagicMock, patch

import pytest

from auth_server.container import AuthContainer


@pytest.mark.unit
@patch("auth_server.container.OAuth2ConfigLoader")
def test_cloud_identity_client_is_scoped_to_google_customer_id(_mock_config_loader):
    settings = MagicMock()
    settings.google_service_account_key_json = "{}"
    settings.google_customer_id = "C046psxkn"

    client = AuthContainer(settings, redis_client=MagicMock()).cloud_identity_client

    assert client._key_json == "{}"
    assert client._customer_id == "C046psxkn"
