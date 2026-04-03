"""
Unit tests for the configuration module.
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from registry.core.config import Settings


@pytest.mark.unit
@pytest.mark.core
class TestSettings:
    """Test suite for Settings configuration."""

    @patch.dict(os.environ, {}, clear=True)
    def test_default_values(self):
        """Test default configuration values."""
        # Create settings without loading .env file
        settings = Settings(_env_file=None)

        assert settings.admin_user == "admin"
        assert settings.admin_password == "password"
        assert settings.session_cookie_name == "jarvis_registry_session"
        assert settings.session_max_age_seconds == 60 * 60 * 8  # 8 hours
        assert settings.local_embeddings_model_name == "all-MiniLM-L6-v2"
        assert settings.local_embeddings_model_dimensions == 384
        assert settings.health_check_interval_seconds == 300  # 5 minutes

    @patch.dict(os.environ, {}, clear=True)
    def test_secret_key_generation(self):
        """Test that secret key is generated if not provided."""
        # Create settings without loading .env file
        settings = Settings(_env_file=None)

        assert settings.secret_key is not None
        assert len(settings.secret_key) == 64  # 32 bytes in hex = 64 characters

    @patch.dict(os.environ, {}, clear=True)
    def test_custom_secret_key(self):
        """Test using custom secret key."""
        custom_key = "my-custom-secret-key"
        settings = Settings(secret_key=custom_key, _env_file=None)

        assert settings.secret_key == custom_key

    @pytest.mark.skip(reason="servers_dir removed in PR-113 (MongoDB migration)")
    @patch.dict(os.environ, {}, clear=True)
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
    @patch.dict(os.environ, {}, clear=True)
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
    @patch.dict(os.environ, {}, clear=True)
    def test_nginx_config_path(self):
        """Test nginx configuration path."""
        settings = Settings()

        assert settings.nginx_config_path == Path("/etc/nginx/conf.d/nginx_rev_proxy.conf")

    @patch.dict(
        "os.environ",
        {
            "ADMIN_USER": "testuser",
            "ADMIN_PASSWORD": "testpass",
            "SECRET_KEY": "test-secret",
            "LOCAL_EMBEDDINGS_MODEL_NAME": "test-model",
            "HEALTH_CHECK_INTERVAL_SECONDS": "120",
        },
    )
    def test_environment_variables(self):
        """Test that environment variables are loaded correctly."""
        settings = Settings()

        assert settings.admin_user == "testuser"
        assert settings.admin_password == "testpass"
        assert settings.secret_key == "test-secret"
        assert settings.local_embeddings_model_name == "test-model"
        assert settings.health_check_interval_seconds == 120

    def test_case_insensitive_env_vars(self):
        """Test that environment variables are case insensitive."""
        with patch.dict(os.environ, {"admin_user": "lowercase_user"}, clear=True):
            settings = Settings()
            assert settings.admin_user == "lowercase_user"

    @patch.dict(
        "os.environ",
        {
            "AZURE_AI_PROJECT_ENDPOINT": "https://example.projects.ai.azure.com",
            "AZURE_CLIENT_ID": "client-id",
            "AZURE_CLIENT_SECRET": "client-secret",
            "AZURE_TENANT_ID": "tenant-id",
        },
        clear=True,
    )
    def test_azure_ai_foundry_environment_variables(self):
        settings = Settings()

        assert settings.azure_ai_project_endpoint == "https://example.projects.ai.azure.com"
        assert settings.azure_client_id == "client-id"
        assert settings.azure_client_secret == "client-secret"
        assert settings.azure_tenant_id == "tenant-id"

    @pytest.mark.skip(reason="servers_dir removed in PR-113 (MongoDB migration)")
    @patch.dict(os.environ, {}, clear=True)
    @patch("pathlib.Path.exists")
    def test_custom_container_paths(self, mock_exists):
        """Test custom container paths."""
        # Mock that /custom/app exists to simulate container environment
        mock_exists.return_value = True
        custom_registry_dir = Path("/custom/registry")

        settings = Settings(container_registry_dir=custom_registry_dir)

        assert settings.container_registry_dir == custom_registry_dir
        assert settings.servers_dir == custom_registry_dir / "servers"
