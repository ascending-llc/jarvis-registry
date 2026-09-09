"""Unit tests for registry_pkgs.core.scopes module."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from registry_pkgs.core import scopes
from registry_pkgs.core.scopes import filter_known_groups, get_scope_description, map_groups_to_scopes


@pytest.fixture
def valid_scopes_config():
    """Valid scopes configuration for testing."""
    return {
        "group_mappings": {
            "admin": ["servers-read", "servers-write"],
            "user": ["servers-read"],
        },
        "servers-read": {
            "description": "View registered MCP servers.",
            "actions": [
                {"action": "list_servers", "method": "GET", "endpoint": "/servers"},
                {"action": "get_server", "method": "GET", "endpoint": "/servers/{server_id}"},
            ],
        },
        "servers-write": {
            "description": "Modify registered MCP servers.",
            "actions": [
                {"action": "create_server", "method": "POST", "endpoint": "/servers"},
                {"action": "delete_server", "method": "DELETE", "endpoint": "/servers/{server_id}"},
            ],
        },
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
    original_cache = scopes._SCOPES_CONFIG_CACHE
    scopes._SCOPES_CONFIG_CACHE = None

    yield

    scopes._SCOPES_CONFIG_CACHE = original_cache


@pytest.fixture
def use_temp_scopes_file(temp_scopes_file, reset_scopes_cache):
    """Point the module at a temp scopes.yml instead of the package-bundled one."""
    with patch("registry_pkgs.core.scopes.get_scopes_file_path", return_value=temp_scopes_file):
        yield temp_scopes_file


class TestGetScopesFilePath:
    """Tests for get_scopes_file_path() function."""

    def test_returns_package_bundled_path(self, reset_scopes_cache):
        """The package-bundled scopes.yml is always used — there is no override."""
        result = scopes.get_scopes_file_path()
        expected_path = Path(scopes.__file__).parent.parent / "scopes.yml"
        assert result == expected_path
        assert result.exists()

    def test_raises_filenotfounderror_when_no_file_found(self, reset_scopes_cache):
        """Test that FileNotFoundError is raised when scopes.yml cannot be found."""
        with patch.object(Path, "exists", return_value=False):
            with pytest.raises(FileNotFoundError) as exc_info:
                scopes.get_scopes_file_path()
            assert "scopes.yml not found" in str(exc_info.value)


class TestLoadScopesConfig:
    """Tests for load_scopes_config() function."""

    def test_loads_valid_scopes_config_successfully(self, use_temp_scopes_file, valid_scopes_config):
        """Test that a valid scopes configuration is loaded successfully."""
        result = scopes.load_scopes_config()
        assert result == valid_scopes_config
        assert "group_mappings" in result
        assert "servers-read" in result
        assert "servers-write" in result

    def test_caches_loaded_config(self, use_temp_scopes_file):
        """Test that configuration is cached after first load."""
        result1 = scopes.load_scopes_config()
        result2 = scopes.load_scopes_config()

        assert result1 is result2
        assert scopes._SCOPES_CONFIG_CACHE

    def test_raises_runtime_error_for_invalid_config_type(self, reset_scopes_cache):
        """Test that RuntimeError is raised if config is not a dict."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            # Write invalid YAML (list instead of dict)
            yaml.dump(["item1", "item2"], f)
            temp_path = Path(f.name)

        try:
            with patch("registry_pkgs.core.scopes.get_scopes_file_path", return_value=temp_path):
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
            with patch("registry_pkgs.core.scopes.get_scopes_file_path", return_value=temp_path):
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
            with patch("registry_pkgs.core.scopes.get_scopes_file_path", return_value=temp_path):
                with pytest.raises(RuntimeError) as exc_info:
                    scopes.load_scopes_config()
            assert "Invalid YAML" in str(exc_info.value)
        finally:
            temp_path.unlink()

    def test_raises_exception_when_file_not_found(self, reset_scopes_cache):
        """Test that exception is raised when scopes file cannot be found."""
        with patch("registry_pkgs.core.scopes.get_scopes_file_path") as mock_get_path:
            mock_get_path.side_effect = FileNotFoundError("scopes.yml not found")
            with pytest.raises(FileNotFoundError):
                scopes.load_scopes_config()

    def test_logs_successful_load_with_group_count(self, use_temp_scopes_file):
        """Test that successful load is logged with group mapping count."""
        with patch("registry_pkgs.core.scopes.logger") as mock_logger:
            scopes.load_scopes_config()
            info_calls = [str(call) for call in mock_logger.info.call_args_list]
            assert any("2 group mappings" in call for call in info_calls)


class TestScopesConfigStructure:
    """Tests for scopes configuration structure validation."""

    def test_validates_group_mappings_structure(self, use_temp_scopes_file):
        """Test that group_mappings has expected structure."""
        config = scopes.load_scopes_config()
        assert "group_mappings" in config
        assert isinstance(config["group_mappings"], dict)
        for group_name, group_scopes in config["group_mappings"].items():
            assert isinstance(group_name, str)
            assert isinstance(group_scopes, list)

    def test_validates_scope_definitions_structure(self, use_temp_scopes_file):
        """Test that scope definitions have expected structure."""
        config = scopes.load_scopes_config()
        for key, value in config.items():
            if key != "group_mappings":
                assert isinstance(value, dict)
                assert isinstance(value["description"], str)
                assert isinstance(value["actions"], list)
                for action in value["actions"]:
                    assert isinstance(action, dict)
                    assert "action" in action
                    assert "method" in action
                    assert "endpoint" in action


class TestEdgeCases:
    """Tests for edge cases and error conditions."""

    def test_handles_empty_group_mappings(self, reset_scopes_cache):
        """Test that empty group_mappings section is accepted."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump({"group_mappings": {}, "servers-read": []}, f)
            temp_path = Path(f.name)

        try:
            with patch("registry_pkgs.core.scopes.get_scopes_file_path", return_value=temp_path):
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
            with patch("registry_pkgs.core.scopes.get_scopes_file_path", return_value=temp_path):
                result = scopes.load_scopes_config()
            assert result["servers-read"] == []
        finally:
            temp_path.unlink()

    def test_cache_persists_after_file_deleted(self, use_temp_scopes_file):
        """Test that cache persists and prevents re-reading the file."""
        result1 = scopes.load_scopes_config()
        use_temp_scopes_file.unlink()
        result2 = scopes.load_scopes_config()
        assert result1 is result2
        assert result2 is not None


class TestMapGroupsToScopes:
    """Tests for map_groups_to_scopes function."""

    def test_maps_known_group(self, use_temp_scopes_file):
        """Known groups are resolved to their configured scopes."""
        result = map_groups_to_scopes(["admin"])

        assert result == ["servers-read", "servers-write"]

    def test_unknown_group_returns_empty(self, use_temp_scopes_file):
        """Unknown groups produce no scopes."""
        result = map_groups_to_scopes(["unknown-group"])

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
            with patch("registry_pkgs.core.scopes.get_scopes_file_path", return_value=temp_path):
                result = map_groups_to_scopes(["group-a", "group-b"])

            assert result == ["scope1", "scope2", "scope3"]
        finally:
            temp_path.unlink()

    def test_empty_groups_list(self, use_temp_scopes_file):
        """Empty groups list returns empty scopes list."""
        result = map_groups_to_scopes([])

        assert result == []

    def test_partial_group_match(self, use_temp_scopes_file):
        """Mix of known and unknown groups returns only known scopes."""
        result = map_groups_to_scopes(["admin", "unknown-group", "user"])

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
            with patch("registry_pkgs.core.scopes.get_scopes_file_path", return_value=temp_path):
                result = map_groups_to_scopes(["group-a", "group-b"])

            # First occurrence order is preserved: scope1, scope2, scope3 from group-a,
            # then scope4 from group-b (scope3 and scope2 already seen)
            assert result == ["scope1", "scope2", "scope3", "scope4"]
        finally:
            temp_path.unlink()


class TestFilterKnownGroups:
    """Tests for filter_known_groups function."""

    def test_known_group_is_kept(self, use_temp_scopes_file):
        """A group present in group_mappings is kept in the output."""
        result = filter_known_groups(["admin"])
        assert result == ["admin"]

    def test_unknown_group_is_removed(self, use_temp_scopes_file):
        """A group absent from group_mappings is removed."""
        result = filter_known_groups(["All-Company-Employees"])
        assert result == []

    def test_mixed_groups_keeps_only_known(self, use_temp_scopes_file):
        """Mix of known and unknown groups returns only the known subset."""
        result = filter_known_groups(["admin", "All-Company-Employees", "user"])
        assert result == ["admin", "user"]

    def test_empty_input_returns_empty(self, use_temp_scopes_file):
        """Empty input list returns empty output."""
        result = filter_known_groups([])
        assert result == []

    def test_preserves_input_order(self, use_temp_scopes_file):
        """Output preserves the original order of known groups."""
        result = filter_known_groups(["user", "admin"])
        assert result == ["user", "admin"]

    def test_does_not_deduplicate(self, use_temp_scopes_file):
        """Duplicate known groups are not removed — mirrors map_groups_to_scopes input convention."""
        result = filter_known_groups(["admin", "admin"])
        assert result == ["admin", "admin"]


class TestGetScopeDescription:
    """Tests for get_scope_description function."""

    def test_returns_description_for_nested_scope(self, use_temp_scopes_file):
        result = get_scope_description("servers-read")

        assert result == "View registered MCP servers."

    def test_returns_none_for_unknown_scope(self, use_temp_scopes_file):
        result = get_scope_description("unknown-scope")

        assert result is None

    def test_returns_none_for_legacy_flat_scope(self, reset_scopes_cache, tmp_path):
        temp_path = tmp_path / "scopes.yml"
        temp_path.write_text(
            yaml.safe_dump(
                {
                    "group_mappings": {},
                    "servers-read": [{"action": "list_servers", "method": "GET", "endpoint": "/servers"}],
                }
            )
        )

        with patch("registry_pkgs.core.scopes.get_scopes_file_path", return_value=temp_path):
            result = get_scope_description("servers-read")

        assert result is None

    def test_returns_description_from_packaged_scopes_config(self, reset_scopes_cache):
        result = get_scope_description("servers-read")

        assert result == "View registered MCP servers, their tools, and connection status."
