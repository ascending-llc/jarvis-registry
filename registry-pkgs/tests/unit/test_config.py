import logging
import os
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from registry_pkgs.core.config import DISABLE_DOTENV_ENV_VAR, JarvisBaseSettings
from registry_pkgs.testing.fixtures import disable_dotenv_loading


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


@pytest.mark.unit
def test_registry_client_origin_strips_path() -> None:
    settings = JarvisBaseSettings(
        registry_url="https://jarvis-demo.ascendingdc.com/gateway",
        registry_client_url="https://jarvis-demo.ascendingdc.com/gateway",
        x_jarvis_registry_import_checks="disabled",
    )
    assert settings.registry_client_origin == "https://jarvis-demo.ascendingdc.com"


@pytest.mark.unit
def test_registry_client_origin_keeps_port() -> None:
    settings = JarvisBaseSettings(
        registry_url="http://localhost:7860",
        registry_client_url="http://localhost:5173",
        x_jarvis_registry_import_checks="disabled",
    )
    assert settings.registry_client_origin == "http://localhost:5173"


def test_dotenv_in_current_directory_is_ignored_when_disabled(tmp_path, monkeypatch) -> None:
    # conftest's bootstrap set JARVIS_DISABLE_DOTENV=1; a `.env` right in the working directory must not load.
    (tmp_path / ".env").write_text("DEPLOYMENT_ENVIRONMENT=from-dotenv\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DEPLOYMENT_ENVIRONMENT", raising=False)
    assert os.environ[DISABLE_DOTENV_ENV_VAR] == "1"

    assert JarvisBaseSettings(x_jarvis_registry_import_checks="disabled").deployment_environment is None


def test_dotenv_in_current_directory_loads_when_not_disabled(tmp_path, monkeypatch) -> None:
    # Control for the test above: without the switch, the same `.env` is read (local dev behavior).
    (tmp_path / ".env").write_text("DEPLOYMENT_ENVIRONMENT=from-dotenv\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DEPLOYMENT_ENVIRONMENT", raising=False)
    monkeypatch.delenv(DISABLE_DOTENV_ENV_VAR)

    assert JarvisBaseSettings(x_jarvis_registry_import_checks="disabled").deployment_environment == "from-dotenv"


def test_real_environment_still_wins_when_dotenv_disabled(tmp_path, monkeypatch) -> None:
    (tmp_path / ".env").write_text("DEPLOYMENT_ENVIRONMENT=from-dotenv\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DEPLOYMENT_ENVIRONMENT", "from-env")

    assert JarvisBaseSettings(x_jarvis_registry_import_checks="disabled").deployment_environment == "from-env"


def test_disable_dotenv_loading_sets_both_guards(monkeypatch) -> None:
    monkeypatch.delenv(DISABLE_DOTENV_ENV_VAR, raising=False)
    monkeypatch.delenv("LITELLM_MODE", raising=False)

    disable_dotenv_loading()

    assert os.environ[DISABLE_DOTENV_ENV_VAR] == "1"
    assert os.environ["LITELLM_MODE"] == "PRODUCTION"
