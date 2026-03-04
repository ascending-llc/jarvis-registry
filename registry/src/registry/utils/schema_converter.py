"""
Schema field naming conversion utilities

Handles conversion between snake_case (API) and camelCase (MongoDB)
"""

import re
from typing import Any


def to_camel_case(snake_str: str) -> str:
    """
    Convert snake_case to camelCase

    Examples:
        server_name -> serverName
        num_tools -> numTools
        created_at -> createdAt
    """
    components = snake_str.split("_")
    return components[0] + "".join(x.title() for x in components[1:])


def to_snake_case(camel_str: str) -> str:
    """
    Convert camelCase to snake_case

    Examples:
        serverName -> server_name
        numTools -> num_tools
        createdAt -> created_at
    """
    snake_str = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", camel_str)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", snake_str).lower()


def convert_dict_keys_to_camel(data: dict[str, Any] | Any) -> dict[str, Any] | Any:
    """
    Recursively convert dictionary keys from snake_case to camelCase

    Used when: API input (snake_case) -> MongoDB storage (camelCase)

    Args:
        data: Dictionary with snake_case keys or any other type

    Returns:
        Dictionary with camelCase keys, or original data if not a dict
    """
    if not isinstance(data, dict):
        return data

    converted = {}
    for key, value in data.items():
        camel_key = to_camel_case(key)

        if isinstance(value, dict):
            converted[camel_key] = convert_dict_keys_to_camel(value)
        elif isinstance(value, list):
            converted[camel_key] = [
                convert_dict_keys_to_camel(item) if isinstance(item, dict) else item for item in value
            ]
        else:
            converted[camel_key] = value

    return converted


def convert_dict_keys_to_snake(data: dict[str, Any] | Any) -> dict[str, Any] | Any:
    """
    Recursively convert dictionary keys from camelCase to snake_case

    Used when: MongoDB data (camelCase) -> API response (snake_case)

    Args:
        data: Dictionary with camelCase keys or any other type

    Returns:
        Dictionary with snake_case keys, or original data if not a dict
    """
    if not isinstance(data, dict):
        return data

    converted = {}
    for key, value in data.items():
        snake_key = to_snake_case(key)

        if isinstance(value, dict):
            converted[snake_key] = convert_dict_keys_to_snake(value)
        elif isinstance(value, list):
            converted[snake_key] = [
                convert_dict_keys_to_snake(item) if isinstance(item, dict) else item for item in value
            ]
        else:
            converted[snake_key] = value

    return converted
