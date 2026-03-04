"""
Base schema classes with automatic snake_case <-> camelCase conversion

All API schemas should inherit from APIBaseModel to ensure consistent
field naming conventions:
- API uses snake_case (external interface)
- MongoDB uses camelCase (internal storage)
- Automatic conversion handled by Pydantic
"""

from typing import Any

from pydantic import BaseModel, ConfigDict


def to_camel_case(snake_str: str) -> str:
    """
    Convert snake_case to camelCase for Pydantic alias generation

    Examples:
        server_name -> serverName
        num_tools -> numTools
        created_at -> createdAt
    """
    components = snake_str.split("_")
    return components[0] + "".join(x.title() for x in components[1:])


class APIBaseModel(BaseModel):
    """
    Base model for all API schemas

    Features:
    - API uses snake_case field names (external)
    - MongoDB uses camelCase field names (internal)
    - Automatic alias generation for camelCase
    - Supports both field name and alias on input
    - API responses use snake_case (by_alias=False by default)
    - Methods for explicit conversion when needed
    """

    model_config = ConfigDict(
        populate_by_name=True,  # Allow both snake_case and camelCase on input
        alias_generator=to_camel_case,  # Auto-generate camelCase aliases for all fields
        # API responses should use field names (snake_case), not aliases
        # This ensures JSON responses match API documentation
        json_schema_serialization_defaults_required=True,
    )

    def model_dump_for_mongo(self, **kwargs) -> dict[str, Any]:
        """
        Dump model for MongoDB storage (camelCase keys)

        This method serializes the model using aliases (camelCase),
        which matches the MongoDB storage format.

        Args:
            **kwargs: Additional arguments passed to model_dump

        Returns:
            Dictionary with camelCase keys suitable for MongoDB
        """
        return self.model_dump(by_alias=True, exclude_none=True, **kwargs)

    def model_dump_for_api(self, **kwargs) -> dict[str, Any]:
        """
        Dump model for API response (snake_case keys)

        This method serializes the model using field names (snake_case),
        which matches the API response format.

        Args:
            **kwargs: Additional arguments passed to model_dump

        Returns:
            Dictionary with snake_case keys suitable for API responses
        """
        return self.model_dump(by_alias=False, exclude_none=True, **kwargs)
