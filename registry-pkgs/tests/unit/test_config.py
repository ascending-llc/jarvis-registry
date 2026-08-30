import logging
import os
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from registry_pkgs.core.config import JarvisBaseSettings


@pytest.mark.unit
@patch.dict(os.environ, {"X_JARVIS_REGISTRY_IMPORT_CHECKS": "disabled"})
def test_validation_disablement(caplog) -> None:
    caplog.set_level(logging.WARNING)

    JarvisBaseSettings()

    assert "JWT_PRIVATE_KEY and JWT_PUBLIC_KEY validation is disabled." in caplog.text


@pytest.mark.unit
def test_auth_server_redis_key_prefix_default_and_env_override() -> None:
    with patch.dict(os.environ, {"X_JARVIS_REGISTRY_IMPORT_CHECKS": "disabled"}, clear=True):
        default_settings = JarvisBaseSettings(_env_file=None)
    with patch.dict(
        os.environ,
        {
            "AUTH_SERVER_REDIS_KEY_PREFIX": "jarvis-auth-server-test",
            "X_JARVIS_REGISTRY_IMPORT_CHECKS": "disabled",
        },
        clear=True,
    ):
        overridden_settings = JarvisBaseSettings(_env_file=None)

    assert default_settings.auth_server_redis_key_prefix == "jarvis-auth-server"
    assert overridden_settings.auth_server_redis_key_prefix == "jarvis-auth-server-test"


@pytest.mark.unit
def test_deployment_environment_env_override_reaches_telemetry_config() -> None:
    with patch.dict(
        os.environ,
        {
            "DEPLOYMENT_ENVIRONMENT": "demo",
            "X_JARVIS_REGISTRY_IMPORT_CHECKS": "disabled",
        },
        clear=True,
    ):
        settings = JarvisBaseSettings(_env_file=None)

    assert settings.deployment_environment == "demo"
    assert settings.telemetry_config.deployment_environment == "demo"


@pytest.mark.unit
@pytest.mark.parametrize("client_id", ["", "   ", "user-generated"])
def test_headless_agent_client_id_rejects_empty_and_interactive_values(client_id: str) -> None:
    with pytest.raises(ValidationError, match="headless_agent_client_id"):
        JarvisBaseSettings(
            headless_agent_client_id=client_id,
            x_jarvis_registry_import_checks="disabled",
        )


@pytest.mark.unit
def test_headless_agent_client_id_rejects_registry_client_id() -> None:
    client_id = "custom-registry-client"

    with pytest.raises(ValidationError, match="must not match registry_app_name"):
        JarvisBaseSettings(
            registry_app_name=client_id,
            headless_agent_client_id=client_id,
            x_jarvis_registry_import_checks="disabled",
        )


@pytest.mark.unit
def test_headless_agent_client_id_allows_custom_value_and_strips_whitespace() -> None:
    settings = JarvisBaseSettings(
        headless_agent_client_id=" custom-headless-agent ",
        x_jarvis_registry_import_checks="disabled",
    )

    assert settings.headless_agent_client_id == "custom-headless-agent"


@pytest.mark.unit
def test_jwt_token_config_carries_headless_agent_client_id_and_all_scopes() -> None:
    settings = JarvisBaseSettings(x_jarvis_registry_import_checks="disabled")
    jtc = settings.jwt_token_config
    assert jtc.headless_agent_client_id == settings.headless_agent_client_id
    assert jtc.all_scopes == frozenset(settings.scopes_list)
