"""Shared packages for MCP Gateway Registry."""

from registry_pkgs.core.scopes import get_scopes_config, load_scopes_config, map_groups_to_scopes

__version__ = "0.1.0"
__all__ = ["load_scopes_config", "get_scopes_config", "map_groups_to_scopes"]
