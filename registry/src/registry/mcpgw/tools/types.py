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
