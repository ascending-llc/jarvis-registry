"""Shared string-serialization helpers for workflow prompt building and debug snapshots.

Kept in a dependency-free module so both compiler.py (for NodeRun.input_snapshot) and
prompt.py (for dependency-output stringification) can import it without creating a cycle.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel


def try_parse_json(value: Any) -> Any:
    """If value is a JSON object/array string, return the parsed object; else return value unchanged.

    Restricted to object/array shapes (not bare JSON scalars) so plain-text prompts that
    happen to be valid JSON primitives (e.g. "123", "true") are not silently retyped.
    """
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped or stripped[0] not in "{[":
        return value
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return value
    return parsed if isinstance(parsed, (dict, list)) else value


def json_safe(value: Any) -> Any:
    """Return a Mongo-safe, debug-friendly representation of workflow input values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if hasattr(value, "to_dict"):
        try:
            return json_safe(value.to_dict())
        except Exception:
            return str(value)
    return str(value)


def content_to_str(content: Any) -> str:
    """Normalize StepOutput.content to a plain string for prompt injection."""
    normalized = json_safe(content)
    if isinstance(normalized, (dict, list)):
        return json.dumps(normalized, ensure_ascii=False, indent=2)
    return str(normalized) if normalized is not None else ""
