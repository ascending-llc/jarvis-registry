from __future__ import annotations

_AUTHORING_TO_AGNO: dict[str, str] = {
    "string": "str",
    "number": "float",
    "boolean": "bool",
    "array": "list",
}

_AGNO_TO_AUTHORING: dict[str, str] = {
    "str": "string",
    "int": "number",
    "float": "number",
    "bool": "boolean",
    "list": "array",
    "string": "string",
    "number": "number",
    "boolean": "boolean",
    "array": "array",
}


def field_type_to_agno(field_type: str) -> str:
    """Map an authoring field-type name to the agno Python type name.

    Unknown values pass through unchanged.
    """
    return _AUTHORING_TO_AGNO.get(field_type, field_type)


def field_type_to_authoring(field_type: str | None) -> str | None:
    """Map an agno (or already-authoring) field-type name to the authoring vocab.

    ``None`` passes through as ``None``; unknown values pass through unchanged.
    """
    if field_type is None:
        return None
    return _AGNO_TO_AUTHORING.get(field_type, field_type)
