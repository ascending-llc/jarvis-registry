import logging
import os
from unittest.mock import patch

import pytest

from auth_server.core.config import AuthSettings


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
        redis_key_prefix="jarvis-auth-server-test",
    )

    assert auth_settings.redis_config.redis_uri == "redis://localhost:6379/9"
    assert auth_settings.redis_config.redis_key_prefix == "jarvis-auth-server-test"
