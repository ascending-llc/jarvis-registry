"""Per-request protocol context for the transparent direct MCP proxy."""

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from mcp_types import CLIENT_CAPABILITIES_META_KEY, ClientCapabilities, ElicitResult
from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS, MODERN_PROTOCOL_VERSIONS

from registry.constants import URL_ELICITATION_INPUT_KEY

# tools/list retains the historical pre-tool-call OAuth discovery behavior.
HANDSHAKE_METHODS = frozenset({"initialize", "server/discover", "tools/list"})
CONSENT_EXEMPT_METHODS = HANDSHAKE_METHODS | {"resources/list", "resources/templates/list", "prompts/list"}
INPUT_REQUIRED_METHODS = frozenset({"tools/call", "prompts/get", "resources/read"})


class ProxyProtocolEra(StrEnum):
    LEGACY = "legacy"
    MODERN = "modern"
    UNKNOWN = "unknown"


def _object_field(container: dict[str, Any], key: str) -> dict[str, Any]:
    if key not in container:
        return {}
    value = container[key]
    if not isinstance(value, dict):
        raise ValueError("Invalid MCP proxy request parameters.")
    return value


@dataclass(frozen=True)
class ProxyCall:
    request_id: str | int
    method: str
    tool_name: str | None
    protocol_era: ProxyProtocolEra
    url_elicitation: bool
    url_answer: Literal["accept", "decline", "cancel"] | None
    body: bytes

    @classmethod
    def from_message(
        cls, request_id: str | int, method: str, message: dict[str, Any], body: bytes, version: str | None
    ) -> "ProxyCall":
        """Interpret only Registry-owned fields; preserve all downstream extension data.

        The authorization input key is reserved on modern direct connections. A
        downstream server must not use it for its own input requests.
        """
        era = ProxyProtocolEra.UNKNOWN
        if version in MODERN_PROTOCOL_VERSIONS:
            era = ProxyProtocolEra.MODERN
        elif version is None or version in HANDSHAKE_PROTOCOL_VERSIONS:
            era = ProxyProtocolEra.LEGACY
        params = message.get("params")
        tool_name = params.get("name") if method == "tools/call" and isinstance(params, dict) else None
        tool_name = tool_name if isinstance(tool_name, str) else None
        supported = era == ProxyProtocolEra.LEGACY
        answer: Literal["accept", "decline", "cancel"] | None = None
        if era == ProxyProtocolEra.MODERN:
            params = _object_field(message, "params")
            meta = _object_field(params, "_meta")
            if CLIENT_CAPABILITIES_META_KEY in meta:
                capabilities = ClientCapabilities.model_validate(meta[CLIENT_CAPABILITIES_META_KEY])
                supported = capabilities.elicitation is not None and capabilities.elicitation.url is not None
            responses = _object_field(params, "inputResponses")
            if URL_ELICITATION_INPUT_KEY in responses:
                answer = ElicitResult.model_validate(responses[URL_ELICITATION_INPUT_KEY]).action
                if answer == "accept":
                    remaining = {key: value for key, value in responses.items() if key != URL_ELICITATION_INPUT_KEY}
                    clean_params = dict(params)
                    if remaining:
                        clean_params["inputResponses"] = remaining
                    else:
                        clean_params.pop("inputResponses")
                    body = json.dumps({**message, "params": clean_params}, ensure_ascii=False).encode("utf-8")
        return cls(request_id, method, tool_name, era, supported, answer, body)
