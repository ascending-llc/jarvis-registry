"""Centralized scopes configuration loader for MCP Gateway services.

This module loads ``scopes.yml`` from an explicit ``ScopesConfig`` object rather than
reading environment variables directly. Callers provide the config, and the loader
resolves the file using this priority:

1. ``ScopesConfig.scopes_config_path`` when it points to an existing file
2. The package-bundled ``scopes.yml`` included with ``registry-pkgs``

Configuration is loaded lazily on first use and cached by resolved file path for the
lifetime of the process. Changes to a loaded ``scopes.yml`` still require a restart.
"""

import logging
from pathlib import Path
from typing import Any

import yaml

from .config import ScopesConfig

logger = logging.getLogger(__name__)

_SCOPES_CONFIG_CACHE: dict[str, dict[str, Any]] = {}


def get_scopes_file_path(config: ScopesConfig) -> Path:
    """Resolve the scopes.yml path from explicit config or the packaged fallback."""
    if config.scopes_config_path:
        scopes_path = Path(config.scopes_config_path)
        if scopes_path.exists():
            logger.info(f"Using scopes config from SCOPES_CONFIG_PATH: {scopes_path}")
            return scopes_path
        logger.warning(f"SCOPES_CONFIG_PATH set to {config.scopes_config_path} but file not found")

    package_path = Path(__file__).parent.parent / "scopes.yml"
    if package_path.exists():
        logger.info(f"Using package-bundled scopes config: {package_path}")
        return package_path

    raise FileNotFoundError(
        "scopes.yml not found. Provide scopes_config_path or ensure scopes.yml is packaged with registry-pkgs."
    )


def load_scopes_config(config: ScopesConfig) -> dict[str, Any]:
    """Load scopes configuration from YAML with path-based lazy caching."""
    if config.scopes_config_path and config.scopes_config_path in _SCOPES_CONFIG_CACHE:
        return _SCOPES_CONFIG_CACHE[config.scopes_config_path]

    scopes_file = get_scopes_file_path(config)
    cache_key = str(scopes_file.resolve())

    if cache_key in _SCOPES_CONFIG_CACHE:
        return _SCOPES_CONFIG_CACHE[cache_key]

    try:
        with open(scopes_file) as f:
            loaded = yaml.safe_load(f)

        if not loaded or not isinstance(loaded, dict):
            raise RuntimeError(f"Invalid scopes configuration: expected dict, got {type(loaded)}")
        if "group_mappings" not in loaded:
            raise RuntimeError("scopes.yml missing required 'group_mappings' section")

        logger.info(f"Loaded scopes config with {len(loaded.get('group_mappings', {}))} group mappings")
        _SCOPES_CONFIG_CACHE[cache_key] = loaded
        if config.scopes_config_path:
            _SCOPES_CONFIG_CACHE[config.scopes_config_path] = loaded
        return loaded

    except yaml.YAMLError as e:
        logger.error(f"Failed to parse scopes.yml: {e}")
        raise RuntimeError(f"Invalid YAML in scopes.yml: {e}") from e
    except Exception as e:
        logger.error(f"Failed to load scopes config from {scopes_file}: {e}")
        raise


def map_groups_to_scopes(groups: list[str], config: ScopesConfig) -> list[str]:
    """Map user groups to OAuth2 scopes using the configured scopes file."""
    group_mappings = load_scopes_config(config).get("group_mappings", {})
    scopes: list[str] = []

    for group in groups:
        if group in group_mappings:
            group_scopes = group_mappings[group]
            scopes.extend(group_scopes)
            logger.debug(f"Mapped group '{group}' to scopes: {group_scopes}")
        else:
            logger.debug(f"No scope mapping found for group: {group}")

    seen: set[str] = set()
    unique_scopes: list[str] = []
    for scope in scopes:
        if scope not in seen:
            seen.add(scope)
            unique_scopes.append(scope)

    logger.info(f"Mapped {len(groups)} groups to {len(unique_scopes)} unique scopes")
    return unique_scopes
