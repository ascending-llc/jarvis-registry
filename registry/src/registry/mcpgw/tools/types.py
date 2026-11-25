from enum import StrEnum
from typing import Any

_JARVIS_REGISTRY_PREFIX = "com.ascendingdc.jarvis-registry/"


class ProxyingMetaKey(StrEnum):
    SUCCESS = f"{_JARVIS_REGISTRY_PREFIX}success"
    SERVER_ID = f"{_JARVIS_REGISTRY_PREFIX}server_id"
    SERVER_PATH = f"{_JARVIS_REGISTRY_PREFIX}server_path"


def get_meta_field(success: bool, server_id: str, server_path: str | None) -> dict[str, Any]:
    return {
        ProxyingMetaKey.SUCCESS: success,
        ProxyingMetaKey.SERVER_ID: server_id,
        ProxyingMetaKey.SERVER_PATH: server_path,
    }


_RESERVED_SECOND_LABELS = frozenset({"modelcontextprotocol", "mcp"})


def is_reserved_meta_key(key: str) -> bool:
    """MCP reserves `_meta` prefixes whose second label is `modelcontextprotocol` or `mcp` (e.g. `io.modelcontextprotocol/`)."""
    prefix, slash, _name = key.partition("/")
    if not slash:
        return False
    labels = prefix.split(".")
    return len(labels) >= 2 and labels[1] in _RESERVED_SECOND_LABELS


def forwardable_meta(meta: dict[str, Any] | None) -> dict[str, Any]:
    """Downstream result `_meta` without MCP-reserved keys.

    Reserved keys describe the downstream connection (its `serverInfo` stamp, a related task), not the gateway's
    response; forwarding them would make the gateway's own result claim the downstream identity.
    """
    return {key: value for key, value in (meta or {}).items() if not is_reserved_meta_key(key)}
