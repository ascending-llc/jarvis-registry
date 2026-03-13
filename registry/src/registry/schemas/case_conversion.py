"""
Base schema classes for API models

All API schemas should inherit from APIBaseModel to ensure consistent
field naming conventions using camelCase throughout.
"""

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict


class APIBaseModel(BaseModel):
    """
    Base model for all API schemas

    Features:
    - Uses camelCase field names for both API and MongoDB
    - All field names are defined directly in camelCase
    - Ensures required fields are included in JSON schema serialization
    - Provides to_db_dict() method for API-to-DB field name mapping
    """

    model_config = ConfigDict(
        json_schema_serialization_defaults_required=True,
    )

    # API to Database field name mapping (can be overridden in subclasses)
    # Example: {"requiresOauth": "requiresOAuth"}
    _field_mapping: ClassVar[dict[str, str]] = {}

    def to_db_dict(self, exclude_unset: bool = False) -> dict[str, Any]:
        """
        Convert API model to database-compatible dict with field name mapping.

        This method applies field name transformations defined in _field_mapping
        to handle cases where API field names differ from database field names.

        Args:
            exclude_unset: If True, exclude fields that were not explicitly set

        Returns:
            Dictionary with database-compatible field names

        Example:
            >>> class MyRequest(APIBaseModel):
            ...     _field_mapping = {"requiresOauth": "requiresOAuth"}
            ...     requiresOauth: bool = False
            >>> req = MyRequest(requiresOauth=True)
            >>> req.to_db_dict()
            {'requiresOAuth': True}
        """
        data = self.model_dump(exclude_unset=exclude_unset)

        # Apply field mapping if defined
        if self._field_mapping:
            mapped_data = {}
            for key, value in data.items():
                db_key = self._field_mapping.get(key, key)
                mapped_data[db_key] = value
            return mapped_data

        return data
