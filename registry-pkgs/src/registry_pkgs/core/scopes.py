"""Centralized scopes configuration loader for MCP Gateway services.

Loads the package-bundled ``scopes.yml`` — the single source of truth for group-to-scope
mappings across every deployment. Per-client values never belong in this OSS repo, so this
module always resolves the file bundled with ``registry-pkgs``; there is no per-deployment
override path. ``group_mappings`` keys are provider-agnostic role names — each auth
provider is responsible for converting its own IdP-native group identifiers into these
names before a group list reaches this module (e.g. Google Workspace groups are
email-addressed, so ``GoogleProvider`` strips the domain before returning its group list).

Configuration is loaded lazily on first use and cached for the lifetime of the process.
"""

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_SCOPES_CONFIG_CACHE: dict[str, Any] | None = None


def get_scopes_file_path() -> Path:
    """Resolve the package-bundled scopes.yml path."""
    package_path = Path(__file__).parent.parent / "scopes.yml"
    if package_path.exists():
        return package_path

    raise FileNotFoundError("scopes.yml not found; ensure scopes.yml is packaged with registry-pkgs.")


def load_scopes_config() -> dict[str, Any]:
    """Load scopes configuration from the package-bundled YAML, cached after first load."""
    global _SCOPES_CONFIG_CACHE
    if _SCOPES_CONFIG_CACHE is not None:
        return _SCOPES_CONFIG_CACHE

    scopes_file = get_scopes_file_path()

    try:
        with open(scopes_file) as f:
            loaded = yaml.safe_load(f)

        if not loaded or not isinstance(loaded, dict):
            raise RuntimeError(f"Invalid scopes configuration: expected dict, got {type(loaded)}")
        if "group_mappings" not in loaded:
            raise RuntimeError("scopes.yml missing required 'group_mappings' section")

        logger.info(f"Loaded scopes config with {len(loaded.get('group_mappings', {}))} group mappings")
        _SCOPES_CONFIG_CACHE = loaded
        return loaded

    except yaml.YAMLError as e:
        logger.error(f"Failed to parse scopes.yml: {e}")
        raise RuntimeError(f"Invalid YAML in scopes.yml: {e}") from e
    except Exception as e:
        logger.error(f"Failed to load scopes config from {scopes_file}: {e}")
        raise


def map_groups_to_scopes(groups: list[str]) -> list[str]:
    """Map user groups to OAuth2 scopes using the bundled scopes.yml."""
    group_mappings = load_scopes_config().get("group_mappings", {})
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


def filter_known_groups(groups: list[str]) -> list[str]:
    """Filter a user's raw IdP groups down to those with a scope mapping in scopes.yml.

    Groups outside `group_mappings` carry no authorization meaning to this application —
    `map_groups_to_scopes` already discards them when deriving scopes. Filtering them out
    before a group list is minted into a JWT keeps that dead weight out of every session
    cookie instead of transmitting and then ignoring it.
    """
    group_mappings = load_scopes_config().get("group_mappings", {})
    return [group for group in groups if group in group_mappings]


def get_scope_description(scope_name: str) -> str | None:
    """Return the lay-person description for a scope, or None if the scope or field is absent."""
    scope_entry = load_scopes_config().get(scope_name)
    return scope_entry.get("description") if isinstance(scope_entry, dict) else None
