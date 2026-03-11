"""
Base schema classes for API models

All API schemas should inherit from APIBaseModel to ensure consistent
field naming conventions using camelCase throughout.
"""

from pydantic import BaseModel, ConfigDict


class APIBaseModel(BaseModel):
    """
    Base model for all API schemas

    Features:
    - Uses camelCase field names for both API and MongoDB
    - All field names are defined directly in camelCase
    - Ensures required fields are included in JSON schema serialization
    """

    model_config = ConfigDict(
        json_schema_serialization_defaults_required=True,
    )
