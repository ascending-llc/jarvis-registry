"""
Unit tests for the configuration module.
"""

import logging
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import ValidationError

from registry.core.config import DEFAULT_AWS_BEDROCK_SONNET_AIP_ARN, Settings

# Module-level RSA key pair so tests that clear os.environ still satisfy the JWT validator.
_TEST_RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)

_SETTINGS_ENV = {
    "JWT_PRIVATE_KEY": _TEST_RSA_KEY.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode(),
    "JWT_PUBLIC_KEY": _TEST_RSA_KEY.public_key()
    .public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    .decode(),
    "TOOL_DISCOVERY_MODE": "external",
    "CREDS_KEY": os.urandom(32).hex(),
    "SECRET_KEY": os.urandom(32).hex(),
}


@pytest.mark.unit
@pytest.mark.core
class TestSettings:
    """Test suite for Settings configuration."""

    @patch.dict(os.environ, _SETTINGS_ENV, clear=True)
    def test_default_values(self):
        """Test default configuration values."""
        # Create settings without loading .env file
        settings = Settings(_env_file=None)

        assert settings.session_cookie_name == "jarvis_registry_session"
        assert settings.session_max_age_seconds == 60 * 60 * 8  # 8 hours
        assert settings.local_embeddings_model_name == "all-MiniLM-L6-v2"
        assert settings.local_embeddings_model_dimensions == 384
        assert settings.health_check_interval_seconds == 300  # 5 minutes

    def test_secret_key_required(self):
        """Test that Settings raises a validation error when SECRET_KEY is absent."""
        env_without_secret_key = {k: v for k, v in _SETTINGS_ENV.items() if k != "SECRET_KEY"}
        with patch.dict(os.environ, env_without_secret_key, clear=True):
            with pytest.raises(ValidationError, match="SECRET_KEY must be set"):
                Settings(_env_file=None)

    @patch.dict(os.environ, _SETTINGS_ENV, clear=True)
    def test_custom_secret_key(self):
        """Test using custom secret key."""
        custom_key = "my-custom-secret-key"
        settings = Settings(secret_key=custom_key, _env_file=None)

        assert settings.secret_key == custom_key

    @pytest.mark.skip(reason="servers_dir removed in PR-113 (MongoDB migration)")
    @patch.dict(os.environ, _SETTINGS_ENV, clear=True)
    @patch("pathlib.Path.exists")
    def test_path_properties(self, mock_exists):
        """Test that path properties return correct paths."""
        # Mock that /app exists to simulate container environment
        mock_exists.return_value = True
        settings = Settings()

        # Test derived paths in container mode
        assert isinstance(settings.container_registry_dir, Path)
        assert settings.servers_dir == settings.container_registry_dir / "servers"
        assert settings.static_dir == settings.container_registry_dir / "static"
        assert settings.templates_dir == settings.container_registry_dir / "templates"
        assert (
            settings.local_embeddings_model_dir
            == settings.container_registry_dir / "models" / settings.local_embeddings_model_name
        )

    @pytest.mark.skip(reason="state_file_path, faiss paths removed in PR-113 (MongoDB migration)")
    @patch.dict(os.environ, _SETTINGS_ENV, clear=True)
    @patch("pathlib.Path.exists")
    def test_file_path_properties(self, mock_exists):
        """Test file path properties."""
        # Mock that /app exists to simulate container environment
        mock_exists.return_value = True
        settings = Settings()

        assert settings.state_file_path == settings.servers_dir / "server_state.json"
        assert settings.log_file_path == Path("/app/logs/registry.log")
        assert settings.faiss_index_path == settings.servers_dir / "service_index.faiss"
        assert settings.faiss_metadata_path == settings.servers_dir / "service_index_metadata.json"
        assert settings.dotenv_path == settings.container_registry_dir / ".env"

    @pytest.mark.skip(reason="nginx_config_path removed in PR-113")
    @patch.dict(os.environ, _SETTINGS_ENV, clear=True)
    def test_nginx_config_path(self):
        """Test nginx configuration path."""
        settings = Settings()

        assert settings.nginx_config_path == Path("/etc/nginx/conf.d/nginx_rev_proxy.conf")

    @patch.dict(
        "os.environ",
        {
            **_SETTINGS_ENV,
            "SECRET_KEY": "test-secret",
            "LOCAL_EMBEDDINGS_MODEL_NAME": "test-model",
            "HEALTH_CHECK_INTERVAL_SECONDS": "120",
        },
    )
    def test_environment_variables(self):
        """Test that environment variables are loaded correctly."""
        settings = Settings()

        assert settings.secret_key == "test-secret"
        assert settings.local_embeddings_model_name == "test-model"
        assert settings.health_check_interval_seconds == 120

    def test_case_insensitive_env_vars(self):
        """Test that environment variables are case insensitive."""
        with patch.dict(os.environ, {**_SETTINGS_ENV, "secret_key": "lowercase_key"}, clear=True):
            settings = Settings()
            assert settings.secret_key == "lowercase_key"

    @pytest.mark.skip(reason="servers_dir removed in PR-113 (MongoDB migration)")
    @patch.dict(os.environ, _SETTINGS_ENV, clear=True)
    @patch("pathlib.Path.exists")
    def test_custom_container_paths(self, mock_exists):
        """Test custom container paths."""
        # Mock that /custom/app exists to simulate container environment
        mock_exists.return_value = True
        custom_registry_dir = Path("/custom/registry")

        settings = Settings(container_registry_dir=custom_registry_dir)

        assert settings.container_registry_dir == custom_registry_dir
        assert settings.servers_dir == custom_registry_dir / "servers"

    @pytest.mark.unit
    @patch.dict(os.environ, {**_SETTINGS_ENV, "X_JARVIS_REGISTRY_IMPORT_CHECKS": "disabled"})
    def test_validation_disablement(self, caplog) -> None:
        caplog.set_level(logging.WARNING)

        Settings()

        for key in ("JWT_PRIVATE_KEY and JWT_PUBLIC_KEY", "CREDS_KEY", "TOOL_DISCOVERY_MODE"):
            assert f"{key} validation is disabled." in caplog.text

    @pytest.mark.unit
    @patch.dict(os.environ, {**_SETTINGS_ENV, "REGISTRY_CLIENT_URL": "http://localhost/gateway"})
    def test_service_urls_validation(self) -> None:
        with pytest.raises(ValueError, match="their path portion must match"):
            Settings()

    @pytest.mark.unit
    @patch.dict(
        os.environ,
        {
            **_SETTINGS_ENV,
            "REGISTRY_URL": "http://localhost/gateway/",
            "REGISTRY_CLIENT_URL": "http://localhost/gateway",
        },
    )
    def test_service_base_path_non_empty(self) -> None:
        settings = Settings()

        assert settings.service_base_path == "/gateway"

    @pytest.mark.unit
    @patch.dict(os.environ, _SETTINGS_ENV)
    def test_service_base_path_empty(self) -> None:
        settings = Settings()

        assert settings.service_base_path == ""

    @pytest.mark.unit
    @patch.dict(os.environ, _SETTINGS_ENV, clear=True)
    def test_aws_bedrock_sonnet_aip_arn_defaults_to_sonnet_aip(self) -> None:
        settings = Settings(_env_file=None)

        assert settings.aws_bedrock_sonnet_aip_arn == DEFAULT_AWS_BEDROCK_SONNET_AIP_ARN
        assert settings.aws_bedrock_require_aip is False

    @pytest.mark.unit
    @patch.dict(
        os.environ,
        {
            **_SETTINGS_ENV,
            "AWS_BEDROCK_SONNET_AIP_ARN": "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/test",
        },
        clear=True,
    )
    def test_aws_bedrock_sonnet_aip_arn_loaded_from_env(self) -> None:
        settings = Settings(_env_file=None)

        assert (
            settings.aws_bedrock_sonnet_aip_arn
            == "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/test"
        )

    @pytest.mark.unit
    @patch.dict(os.environ, _SETTINGS_ENV, clear=True)
    def test_workflow_llm_model_id_falls_back_to_model_id(self) -> None:
        settings = Settings(aws_bedrock_sonnet_aip_arn="", _env_file=None)

        assert settings.workflow_llm_model_id == "amazon.nova-2-lite-v1:0"

    @pytest.mark.unit
    @patch.dict(os.environ, _SETTINGS_ENV, clear=True)
    def test_workflow_llm_model_id_uses_default_sonnet_aip(self) -> None:
        settings = Settings(_env_file=None)

        assert settings.workflow_llm_model_id == DEFAULT_AWS_BEDROCK_SONNET_AIP_ARN

    @pytest.mark.unit
    @patch.dict(
        os.environ,
        {
            **_SETTINGS_ENV,
            "AWS_BEDROCK_SONNET_AIP_ARN": " arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/test ",
        },
        clear=True,
    )
    def test_workflow_llm_model_id_prefers_trimmed_aip_arn(self) -> None:
        settings = Settings(_env_file=None)

        assert (
            settings.workflow_llm_model_id
            == "arn:aws:bedrock:us-east-1:123456789012:application-inference-profile/test"
        )

    @pytest.mark.unit
    @patch.dict(
        os.environ,
        {
            **_SETTINGS_ENV,
            "AWS_BEDROCK_SONNET_AIP_ARN": "",
            "AWS_BEDROCK_REQUIRE_AIP": "true",
        },
        clear=True,
    )
    def test_workflow_llm_model_id_requires_aip_when_configured(self) -> None:
        settings = Settings(_env_file=None)

        with pytest.raises(ValueError, match="AWS_BEDROCK_SONNET_AIP_ARN must be set"):
            _ = settings.workflow_llm_model_id
