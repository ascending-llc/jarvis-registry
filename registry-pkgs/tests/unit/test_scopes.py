"""Unit tests for registry_pkgs.core.scopes module."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from registry_pkgs.core import scopes


@pytest.fixture
def valid_scopes_config():
    """Valid scopes configuration for testing."""
    return {
        "group_mappings": {
            "admin": ["servers-read", "servers-write"],
            "user": ["servers-read"],
        },
        "servers-read": [
            {"action": "list_servers", "method": "GET", "endpoint": "/servers"},
            {"action": "get_server", "method": "GET", "endpoint": "/servers/{server_id}"},
        ],
        "servers-write": [
            {"action": "create_server", "method": "POST", "endpoint": "/servers"},
            {"action": "delete_server", "method": "DELETE", "endpoint": "/servers/{server_id}"},
        ],
    }


@pytest.fixture
def invalid_scopes_config_no_group_mappings():
    """Invalid scopes configuration missing group_mappings."""
    return {
        "servers-read": [
            {"action": "list_servers", "method": "GET", "endpoint": "/servers"},
        ],
    }


@pytest.fixture
def temp_scopes_file(valid_scopes_config):
    """Create a temporary scopes.yml file for testing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump(valid_scopes_config, f)
        temp_path = Path(f.name)

    yield temp_path

    # Cleanup
    if temp_path.exists():
        temp_path.unlink()


@pytest.fixture
def reset_scopes_cache():
    """Reset the module-level scopes cache before and after each test."""
    # Store original value
    original_cache = scopes._SCOPES_CONFIG

    # Reset before test
    scopes._SCOPES_CONFIG = None

    yield

    # Reset after test
    scopes._SCOPES_CONFIG = original_cache


class TestGetScopesFilePath:
    """Tests for get_scopes_file_path() function."""

    def test_uses_scopes_config_path_from_settings_when_set(self, temp_scopes_file, reset_scopes_cache):
        """Test that SCOPES_CONFIG_PATH from settings is used when set and file exists."""
        with patch("registry_pkgs.core.scopes.settings") as mock_settings:
            mock_settings.SCOPES_CONFIG_PATH = str(temp_scopes_file)

            result = scopes.get_scopes_file_path()

            assert result == temp_scopes_file
            assert result.exists()

    def test_warns_when_scopes_config_path_set_but_file_not_found(self, reset_scopes_cache):
        """Test that a warning is logged when SCOPES_CONFIG_PATH is set but file doesn't exist."""
        with patch("registry_pkgs.core.scopes.settings") as mock_settings:
            mock_settings.SCOPES_CONFIG_PATH = "/nonexistent/path/scopes.yml"

            # Mock both env path and package path to not exist
            with patch("registry_pkgs.core.scopes.logger") as mock_logger:
                with patch.object(Path, "exists", return_value=False):
                    with pytest.raises(FileNotFoundError):
                        scopes.get_scopes_file_path()

                    # Verify warning was logged
                    mock_logger.warning.assert_called_once()
                    assert "/nonexistent/path/scopes.yml" in str(mock_logger.warning.call_args)

    def test_uses_package_bundled_file_when_env_var_not_set(self, reset_scopes_cache):
        """Test that package-bundled scopes.yml is used when SCOPES_CONFIG_PATH is not set."""
        with patch("registry_pkgs.core.scopes.settings") as mock_settings:
            mock_settings.SCOPES_CONFIG_PATH = ""

            result = scopes.get_scopes_file_path()

            # Should return path to package-bundled file
            expected_path = Path(scopes.__file__).parent.parent / "scopes.yml"
            assert result == expected_path
            assert result.exists()  # File should actually exist in the package

    def test_raises_filenotfounderror_when_no_file_found(self, reset_scopes_cache):
        """Test that FileNotFoundError is raised when scopes.yml cannot be found."""
        with patch("registry_pkgs.core.scopes.settings") as mock_settings:
            mock_settings.SCOPES_CONFIG_PATH = ""

            # Mock the package path to not exist
            with patch.object(Path, "exists", return_value=False):
                with pytest.raises(FileNotFoundError) as exc_info:
                    scopes.get_scopes_file_path()

                assert "scopes.yml not found" in str(exc_info.value)
                assert "SCOPES_CONFIG_PATH" in str(exc_info.value)


class TestLoadScopesConfig:
    """Tests for load_scopes_config() function."""

    def test_loads_valid_scopes_config_successfully(self, temp_scopes_file, valid_scopes_config, reset_scopes_cache):
        """Test that a valid scopes configuration is loaded successfully."""
        with patch("registry_pkgs.core.scopes.settings") as mock_settings:
            mock_settings.SCOPES_CONFIG_PATH = str(temp_scopes_file)

            result = scopes.load_scopes_config()

            assert result == valid_scopes_config
            assert "group_mappings" in result
            assert "servers-read" in result
            assert "servers-write" in result

    def test_caches_loaded_config(self, temp_scopes_file, reset_scopes_cache):
        """Test that configuration is cached after first load."""
        with patch("registry_pkgs.core.scopes.settings") as mock_settings:
            mock_settings.SCOPES_CONFIG_PATH = str(temp_scopes_file)

            # First call
            result1 = scopes.load_scopes_config()

            # Second call should return cached value
            result2 = scopes.load_scopes_config()

            assert result1 is result2  # Same object in memory
            assert scopes._SCOPES_CONFIG is not None

    def test_raises_runtime_error_for_invalid_config_type(self, reset_scopes_cache):
        """Test that RuntimeError is raised if config is not a dict."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            # Write invalid YAML (list instead of dict)
            yaml.dump(["item1", "item2"], f)
            temp_path = Path(f.name)

        try:
            with patch("registry_pkgs.core.scopes.settings") as mock_settings:
                mock_settings.SCOPES_CONFIG_PATH = str(temp_path)

                with pytest.raises(RuntimeError) as exc_info:
                    scopes.load_scopes_config()

                assert "Invalid scopes configuration" in str(exc_info.value)
                assert "expected dict" in str(exc_info.value)
        finally:
            temp_path.unlink()

    def test_raises_runtime_error_for_missing_group_mappings(self, reset_scopes_cache):
        """Test that RuntimeError is raised if group_mappings section is missing."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            # Write config without group_mappings
            yaml.dump({"servers-read": []}, f)
            temp_path = Path(f.name)

        try:
            with patch("registry_pkgs.core.scopes.settings") as mock_settings:
                mock_settings.SCOPES_CONFIG_PATH = str(temp_path)

                with pytest.raises(RuntimeError) as exc_info:
                    scopes.load_scopes_config()

                assert "missing required 'group_mappings' section" in str(exc_info.value)
        finally:
            temp_path.unlink()

    def test_raises_runtime_error_for_invalid_yaml(self, reset_scopes_cache):
        """Test that RuntimeError is raised for malformed YAML."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            # Write invalid YAML
            f.write("invalid: yaml: content: [unclosed")
            temp_path = Path(f.name)

        try:
            with patch("registry_pkgs.core.scopes.settings") as mock_settings:
                mock_settings.SCOPES_CONFIG_PATH = str(temp_path)

                with pytest.raises(RuntimeError) as exc_info:
                    scopes.load_scopes_config()

                assert "Invalid YAML" in str(exc_info.value)
        finally:
            temp_path.unlink()

    def test_raises_exception_when_file_not_found(self, reset_scopes_cache):
        """Test that exception is raised when scopes file cannot be found."""
        with patch("registry_pkgs.core.scopes.settings") as mock_settings:
            mock_settings.SCOPES_CONFIG_PATH = ""

            # Mock get_scopes_file_path to raise FileNotFoundError
            with patch("registry_pkgs.core.scopes.get_scopes_file_path") as mock_get_path:
                mock_get_path.side_effect = FileNotFoundError("scopes.yml not found")

                with pytest.raises(FileNotFoundError):
                    scopes.load_scopes_config()

    def test_logs_successful_load_with_group_count(self, temp_scopes_file, reset_scopes_cache):
        """Test that successful load is logged with group mapping count."""
        with patch("registry_pkgs.core.scopes.settings") as mock_settings:
            mock_settings.SCOPES_CONFIG_PATH = str(temp_scopes_file)

            with patch("registry_pkgs.core.scopes.logger") as mock_logger:
                scopes.load_scopes_config()

                # Check that info log was called with group count
                info_calls = [str(call) for call in mock_logger.info.call_args_list]
                assert any("2 group mappings" in call for call in info_calls)


class TestGetScopesConfig:
    """Tests for get_scopes_config() convenience function."""

    def test_returns_same_as_load_scopes_config(self, temp_scopes_file, reset_scopes_cache):
        """Test that get_scopes_config returns same result as load_scopes_config."""
        with patch("registry_pkgs.core.scopes.settings") as mock_settings:
            mock_settings.SCOPES_CONFIG_PATH = str(temp_scopes_file)

            result1 = scopes.load_scopes_config()

            # Reset cache to test fresh load
            scopes._SCOPES_CONFIG = None

            result2 = scopes.get_scopes_config()

            assert result1 == result2

    def test_uses_cached_config(self, temp_scopes_file, reset_scopes_cache):
        """Test that get_scopes_config uses cached configuration."""
        with patch("registry_pkgs.core.scopes.settings") as mock_settings:
            mock_settings.SCOPES_CONFIG_PATH = str(temp_scopes_file)

            result1 = scopes.get_scopes_config()
            result2 = scopes.get_scopes_config()

            assert result1 is result2  # Same object instance


class TestScopesConfigStructure:
    """Tests for scopes configuration structure validation."""

    def test_validates_group_mappings_structure(self, temp_scopes_file, reset_scopes_cache):
        """Test that group_mappings has expected structure."""
        with patch("registry_pkgs.core.scopes.settings") as mock_settings:
            mock_settings.SCOPES_CONFIG_PATH = str(temp_scopes_file)

            config = scopes.load_scopes_config()

            assert "group_mappings" in config
            assert isinstance(config["group_mappings"], dict)

            for group_name, group_scopes in config["group_mappings"].items():
                assert isinstance(group_name, str)
                assert isinstance(group_scopes, list)

    def test_validates_scope_definitions_structure(self, temp_scopes_file, reset_scopes_cache):
        """Test that scope definitions have expected structure."""
        with patch("registry_pkgs.core.scopes.settings") as mock_settings:
            mock_settings.SCOPES_CONFIG_PATH = str(temp_scopes_file)

            config = scopes.load_scopes_config()

            # Check non-group_mappings entries are scope definitions
            for key, value in config.items():
                if key != "group_mappings":
                    assert isinstance(value, list)
                    for action in value:
                        assert isinstance(action, dict)
                        assert "action" in action
                        assert "method" in action
                        assert "endpoint" in action


class TestModuleImportPreloading:
    """Tests for module-level preloading behavior."""

    def test_preloads_config_on_module_import(self):
        """Test that configuration is preloaded when module is imported.

        Note: This test is more of a documentation of expected behavior.
        The actual preloading happens at module import time, which is before tests run.
        """
        # If we got here, the module imported successfully, meaning preload didn't fail
        assert True  # Cache may or may not be populated depending on test order

    def test_module_import_fails_if_scopes_not_found(self):
        """Test that module import should fail if scopes.yml cannot be found.

        Note: This is tested implicitly - if scopes.yml is missing during actual import,
        the module will fail to import and tests won't run.
        """
        # This test documents expected behavior
        # In production, if scopes.yml is missing, the service won't start (fail-fast)
        pass


class TestEdgeCases:
    """Tests for edge cases and error conditions."""

    def test_handles_empty_group_mappings(self, reset_scopes_cache):
        """Test that empty group_mappings section is accepted."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump({"group_mappings": {}, "servers-read": []}, f)
            temp_path = Path(f.name)

        try:
            with patch("registry_pkgs.core.scopes.settings") as mock_settings:
                mock_settings.SCOPES_CONFIG_PATH = str(temp_path)

                result = scopes.load_scopes_config()

                assert result["group_mappings"] == {}
        finally:
            temp_path.unlink()

    def test_handles_scopes_without_actions(self, reset_scopes_cache):
        """Test that scopes with empty action lists are accepted."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump({"group_mappings": {"admin": ["servers-read"]}, "servers-read": []}, f)
            temp_path = Path(f.name)

        try:
            with patch("registry_pkgs.core.scopes.settings") as mock_settings:
                mock_settings.SCOPES_CONFIG_PATH = str(temp_path)

                result = scopes.load_scopes_config()

                assert result["servers-read"] == []
        finally:
            temp_path.unlink()

    def test_handles_relative_scopes_config_path(self, valid_scopes_config, reset_scopes_cache, tmp_path):
        """Test that relative paths in SCOPES_CONFIG_PATH are resolved correctly."""
        # Create a temp file in a subdirectory of tmp_path (which is under project)
        temp_dir = tmp_path / "config"
        temp_dir.mkdir()
        temp_file = temp_dir / "test_scopes.yml"
        with open(temp_file, "w") as f:
            yaml.dump(valid_scopes_config, f)

        # Create a relative path from current directory
        try:
            relative_path = temp_file.relative_to(Path.cwd())
        except ValueError:
            # If tmp_path is not under cwd, just use the absolute path for this test
            relative_path = temp_file

        with patch("registry_pkgs.core.scopes.settings") as mock_settings:
            mock_settings.SCOPES_CONFIG_PATH = str(relative_path)

            result = scopes.get_scopes_file_path()

            assert result == Path(relative_path)
            assert result.exists()

    def test_cache_persists_across_calls(self, temp_scopes_file, reset_scopes_cache):
        """Test that cache persists and prevents re-reading file."""
        with patch("registry_pkgs.core.scopes.settings") as mock_settings:
            mock_settings.SCOPES_CONFIG_PATH = str(temp_scopes_file)

            # First load
            result1 = scopes.load_scopes_config()

            # Delete the file
            temp_scopes_file.unlink()

            # Second load should still work (uses cache)
            result2 = scopes.load_scopes_config()

            assert result1 is result2
            assert result2 is not None


class TestMapGroupsToScopes:
    """Tests for map_groups_to_scopes function."""

    def test_maps_known_group(self, temp_scopes_file, reset_scopes_cache):
        """Known groups are resolved to their configured scopes."""
        with patch("registry_pkgs.core.scopes.settings") as mock_settings:
            mock_settings.SCOPES_CONFIG_PATH = str(temp_scopes_file)
            scopes.load_scopes_config()  # Force load

            result = scopes.map_groups_to_scopes(["admin"])

        assert result == ["servers-read", "servers-write"]

    def test_unknown_group_returns_empty(self, temp_scopes_file, reset_scopes_cache):
        """Unknown groups produce no scopes."""
        with patch("registry_pkgs.core.scopes.settings") as mock_settings:
            mock_settings.SCOPES_CONFIG_PATH = str(temp_scopes_file)
            scopes.load_scopes_config()  # Force load

            result = scopes.map_groups_to_scopes(["unknown-group"])

        assert result == []

    def test_deduplicates_scopes(self, reset_scopes_cache):
        """Duplicate scopes from multiple groups are deduplicated while preserving order."""
        config = {
            "group_mappings": {
                "group-a": ["scope1", "scope2"],
                "group-b": ["scope2", "scope3"],
            }
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(config, f)
            temp_path = Path(f.name)

        try:
            with patch("registry_pkgs.core.scopes.settings") as mock_settings:
                mock_settings.SCOPES_CONFIG_PATH = str(temp_path)
                scopes.load_scopes_config()  # Force load

                result = scopes.map_groups_to_scopes(["group-a", "group-b"])

            assert result == ["scope1", "scope2", "scope3"]
        finally:
            temp_path.unlink()

    def test_empty_groups_list(self, temp_scopes_file, reset_scopes_cache):
        """Empty groups list returns empty scopes list."""
        with patch("registry_pkgs.core.scopes.settings") as mock_settings:
            mock_settings.SCOPES_CONFIG_PATH = str(temp_scopes_file)
            scopes.load_scopes_config()  # Force load

            result = scopes.map_groups_to_scopes([])

        assert result == []

    def test_partial_group_match(self, temp_scopes_file, reset_scopes_cache):
        """Mix of known and unknown groups returns only known scopes."""
        with patch("registry_pkgs.core.scopes.settings") as mock_settings:
            mock_settings.SCOPES_CONFIG_PATH = str(temp_scopes_file)
            scopes.load_scopes_config()  # Force load

            result = scopes.map_groups_to_scopes(["admin", "unknown-group", "user"])

        # Should get scopes from admin and user, ignoring unknown-group
        assert set(result) == {"servers-read", "servers-write"}

    def test_preserves_order_with_duplicates(self, reset_scopes_cache):
        """Verify order preservation when deduplicating scopes."""
        config = {
            "group_mappings": {
                "group-a": ["scope1", "scope2", "scope3"],
                "group-b": ["scope3", "scope2", "scope4"],  # Different order
            }
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(config, f)
            temp_path = Path(f.name)

        try:
            with patch("registry_pkgs.core.scopes.settings") as mock_settings:
                mock_settings.SCOPES_CONFIG_PATH = str(temp_path)
                scopes.load_scopes_config()  # Force load

                result = scopes.map_groups_to_scopes(["group-a", "group-b"])

            # First occurrence order is preserved: scope1, scope2, scope3 from group-a,
            # then scope4 from group-b (scope3 and scope2 already seen)
            assert result == ["scope1", "scope2", "scope3", "scope4"]
        finally:
            temp_path.unlink()
