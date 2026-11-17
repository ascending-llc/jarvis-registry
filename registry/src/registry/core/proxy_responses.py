"""Protocol response construction for Registry-injected direct MCP proxy replies."""

from typing import Any
from uuid import uuid4

from fastapi.responses import JSONResponse
from mcp_types import (
    CallToolResult,
    ElicitationRequiredErrorData,
    ElicitRequest,
    ElicitRequestURLParams,
    InputRequiredResult,
    TextContent,
)

from registry.constants import (
    MCP_REQUEST_REFUSED_ERROR_CODE,
    URL_ELICITATION_INPUT_KEY,
)
from registry.core.proxy_call import INPUT_REQUIRED_METHODS, ProxyCall, ProxyProtocolEra

# Custom JSON-RPC error code signaling that a URL mode elicitation (out-of-band browser step) is
# required before the request can proceed — shared by the downstream-OAuth-expired and
# consent-required elicitation paths.
_URL_ELICITATION_REQUIRED_ERROR_CODE = -32042
_AUTHORIZATION_DECLINED_MESSAGE = (
    "The user declined or cancelled the authorization, so the request was not performed. "
    "Do not retry unless the user asks you to."
)


def _build_jsonrpc_error_result(request_id: str | int | None, error_text: str) -> dict[str, Any]:
    """Build JSON-RPC result response with isError=true."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {"content": [{"type": "text", "text": error_text}], "isError": True},
    }


def build_jsonrpc_error(request_id: str | int | None, code: int, message: str, data: Any = None) -> dict[str, Any]:
    """Build JSON-RPC error response."""
    error_response = {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
    if data is not None:
        error_response["error"]["data"] = data
    return error_response


def build_method_error(
    request_id: str | int | None,
    message: str,
    *,
    method: str | None,
    code: int = MCP_REQUEST_REFUSED_ERROR_CODE,
) -> dict[str, Any]:
    """Shape a gateway-generated failure for the method that triggered it."""
    if method == "tools/call":
        return _build_jsonrpc_error_result(request_id, message)
    return build_jsonrpc_error(request_id, code, message)


def _authorization_text_response(call: ProxyCall, message: str) -> JSONResponse:
    if call.protocol_era == ProxyProtocolEra.MODERN and call.method == "tools/call":
        result = CallToolResult(content=[TextContent(type="text", text=message)], is_error=True)
        content = {
            "jsonrpc": "2.0",
            "id": call.request_id,
            "result": result.model_dump(by_alias=True, mode="json", exclude_none=True),
        }
    else:
        content = build_jsonrpc_error(call.request_id, MCP_REQUEST_REFUSED_ERROR_CODE, message)
    return JSONResponse(content=content)


def url_elicitation_response(
    call: ProxyCall, *, auth_url: str, llm_msg: str, user_msg: str, legacy_elicitation_id: str | None = None
) -> JSONResponse:
    if call.protocol_era == ProxyProtocolEra.LEGACY:
        data = ElicitationRequiredErrorData(
            elicitations=[ElicitRequestURLParams(message=user_msg, url=auth_url, elicitation_id=legacy_elicitation_id)]
        ).model_dump(by_alias=True, mode="json", exclude_none=True)
        return JSONResponse(
            content=build_jsonrpc_error(call.request_id, _URL_ELICITATION_REQUIRED_ERROR_CODE, llm_msg, data)
        )
    if call.url_answer in {"decline", "cancel"}:
        return _authorization_text_response(call, _AUTHORIZATION_DECLINED_MESSAGE)
    if (
        call.protocol_era == ProxyProtocolEra.MODERN
        and call.url_answer is None
        and call.method in INPUT_REQUIRED_METHODS
        and call.url_elicitation
    ):
        result = InputRequiredResult(
            input_requests={
                URL_ELICITATION_INPUT_KEY: ElicitRequest(params=ElicitRequestURLParams(message=user_msg, url=auth_url))
            }
        )
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "id": call.request_id,
                "result": result.model_dump(by_alias=True, mode="json", exclude_none=True),
            }
        )
    return _authorization_text_response(call, f"{llm_msg} URL `{auth_url}`")


def authorization_declined_response(call: ProxyCall) -> JSONResponse:
    return _authorization_text_response(call, _AUTHORIZATION_DECLINED_MESSAGE)


def server_consent_response(call: ProxyCall, *, auth_url: str, server_name: str) -> JSONResponse:
    llm_msg = (
        "Jarvis Registry needs the user's explicit consent before this client can call the "
        f"'{server_name}' MCP server. Please direct the user to open the provided URL in a browser "
        "window, grant consent, and come back to retry the same request again."
    )
    user_msg = (
        "Jarvis Registry needs your explicit consent before this application can call the "
        f"'{server_name}' MCP server on your behalf. Please follow the URL to review and approve."
    )
    return url_elicitation_response(
        call,
        auth_url=auth_url,
        llm_msg=llm_msg,
        user_msg=user_msg,
        legacy_elicitation_id=str(uuid4()) if call.protocol_era == ProxyProtocolEra.LEGACY else None,
    )
