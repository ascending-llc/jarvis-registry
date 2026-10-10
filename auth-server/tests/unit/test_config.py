import logging
import os
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from auth_server.core.config import AuthSettings
from auth_server.utils.config_loader import OAuth2ConfigLoader
from registry_pkgs.core.scope_groups import SCOPE_GROUP_OBJECT_ID_FIELDS
from registry_pkgs.testing.fixtures import TEST_SCOPE_GROUP_OBJECT_IDS


@pytest.mark.unit
@patch.dict(os.environ, {"X_JARVIS_REGISTRY_IMPORT_CHECKS": "disabled"})
def test_validation_disablement(caplog) -> None:
    caplog.set_level(logging.WARNING)

    AuthSettings()

    assert "JWT_PRIVATE_KEY and JWT_PUBLIC_KEY validation is disabled." in caplog.text


@pytest.mark.unit
def test_redis_config_uses_auth_server_settings() -> None:
    auth_settings = AuthSettings(
        redis_uri="redis://localhost:6379/9",
        auth_server_redis_key_prefix="jarvis-auth-server-test",
    )

    assert auth_settings.redis_config.redis_uri == "redis://localhost:6379/9"
    assert auth_settings.redis_config.redis_key_prefix == "jarvis-auth-server-test"


@pytest.mark.unit
@patch.dict(
    os.environ,
    {
        "AUTH_SERVER_REDIS_KEY_PREFIX": "jarvis-auth-server-env",
        "X_JARVIS_REGISTRY_IMPORT_CHECKS": "disabled",
    },
    clear=True,
)
def test_auth_server_redis_key_prefix_uses_shared_environment_variable() -> None:
    auth_settings = AuthSettings(_env_file=None)

    assert auth_settings.auth_server_redis_key_prefix == "jarvis-auth-server-env"
    assert auth_settings.redis_config.redis_key_prefix == "jarvis-auth-server-env"


@pytest.mark.unit
@pytest.mark.parametrize("enabled", ["true", "TRUE", " true ", ""])
def test_google_default_still_validates_enabled_entra_bindings(enabled: str) -> None:
    with pytest.raises(ValidationError, match="JARVIS_REGISTRY_ADMIN_GROUP_OBJECT_ID"):
        AuthSettings(
            auth_provider="google",
            entra_enabled=enabled,
            x_jarvis_registry_import_checks="enabled",
            **dict.fromkeys(SCOPE_GROUP_OBJECT_ID_FIELDS.values()),
        )


@pytest.mark.unit
@pytest.mark.parametrize("enabled", ["false", "FALSE", " false ", " "])
def test_google_only_auth_settings_follow_loader_enablement(enabled: str) -> None:
    settings = AuthSettings(
        auth_provider="google",
        entra_enabled=enabled,
        x_jarvis_registry_import_checks="enabled",
        **dict.fromkeys(SCOPE_GROUP_OBJECT_ID_FIELDS.values()),
    )
    assert OAuth2ConfigLoader(settings).get_provider_config("entra")["enabled"] is False


@pytest.mark.unit
def test_google_default_normalizes_enabled_entra_bindings() -> None:
    values = {field: value.upper() for field, value in TEST_SCOPE_GROUP_OBJECT_IDS.items()}
    settings = AuthSettings(auth_provider="google", entra_enabled="true", **values)
    assert {field: getattr(settings, field) for field in values} == TEST_SCOPE_GROUP_OBJECT_IDS


@pytest.mark.unit
@pytest.mark.parametrize("value", ["invalid", TEST_SCOPE_GROUP_OBJECT_IDS["jarvis_registry_user_group_object_id"]])
def test_google_default_rejects_invalid_or_duplicate_enabled_entra_binding(value: str) -> None:
    with pytest.raises(ValidationError, match="JARVIS_REGISTRY_ADMIN_GROUP_OBJECT_ID"):
        AuthSettings(auth_provider="google", jarvis_registry_admin_group_object_id=value)


@pytest.mark.unit
def test_google_default_can_skip_entra_checks_for_ci_imports() -> None:
    AuthSettings(
        auth_provider="google",
        entra_enabled="true",
        x_jarvis_registry_import_checks="disabled",
        **dict.fromkeys(SCOPE_GROUP_OBJECT_ID_FIELDS.values()),
    )
