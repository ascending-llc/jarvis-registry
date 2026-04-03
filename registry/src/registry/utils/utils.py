import logging
import re
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from fastapi import status as http_status

from registry_pkgs.models import ResourceType

logger = logging.getLogger(__name__)

# Template directory - go up two levels from utils.py to registry/, then to templates/oauth/
TEMPLATE_DIR = Path(__file__).parent.parent / "templates" / "oauth"


def load_template(template_name: str, context: dict[str, str]) -> str:
    """Load and render HTML template"""
    template_path = TEMPLATE_DIR / template_name
    if not template_path.exists():
        logger.error(f"Template not found: {template_path}")
        return f"<h1>Template Error</h1><p>Template {template_name} not found</p>"
    try:
        content = None
        with open(template_path, encoding="utf-8") as f:
            content = f.read()

        # Simple template rendering - handle both {{ key }} and {{ {key} }} formats
        for key, value in context.items():
            content = content.replace(f"{{{{ {key} }}}}", str(value))
            content = content.replace(f"{{{{{key}}}}}", str(value))
        return content
    except Exception as e:
        logger.error(f"Failed to load template {template_name}: {e}")
        return f"<h1>Template Error</h1><p>Failed to load template: {e}</p>"


def normalize_headers(config_headers: Any) -> dict[str, str]:
    """
    Normalize headers from dict or list-of-dict into a flat dict[str, str].
    """
    if isinstance(config_headers, dict):
        header_dicts = [config_headers]
    elif isinstance(config_headers, list):
        header_dicts = config_headers
    else:
        header_dicts = []

    normalized: dict[str, str] = {}
    for header_dict in header_dicts:
        if isinstance(header_dict, dict):
            for key, value in header_dict.items():
                if isinstance(value, list):
                    normalized[key] = ", ".join(str(v) for v in value)
                elif value is not None:
                    normalized[key] = str(value)

    return normalized


# ACL utility function
def validate_resource_type(resource_type: str) -> None:
    if resource_type not in [rt.value for rt in ResourceType]:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_resource_type", "message": f"Resource type '{resource_type}' is not valid."},
        )


def generate_server_name_from_title(title: str) -> str:
    """
    Generate a URL-safe slug from a server title.
    """
    slug = title.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)  # Remove special chars except spaces and hyphens
    slug = re.sub(r"\s+", "-", slug)  # Replace spaces with hyphens
    slug = re.sub(r"-+", "-", slug)  # Remove consecutive hyphens
    slug = slug.strip("-")  # Trim leading/trailing hyphens
    return slug or "mcp-server"
