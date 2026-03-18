"""
Version management for MCP Gateway Registry.

Version is determined from BUILD_VERSION environment variable.
"""

import logging

from .core.config import settings

logger = logging.getLogger(__name__)

DEFAULT_VERSION = "1.0.0"


def get_version() -> str:
    """
    Get application version from BUILD_VERSION environment variable.

    Returns:
        Version string from BUILD_VERSION env var or DEFAULT_VERSION
    """
    build_version = settings.build_version.strip()
    if build_version:
        logger.info(f"Version from BUILD_VERSION: {build_version}")
        return build_version

    logger.info(f"Using default version: {DEFAULT_VERSION}")
    return DEFAULT_VERSION


# Module-level version constant
__version__ = get_version()
