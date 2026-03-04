"""
MCP proxy session route.
"""

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from registry.auth.dependencies import CurrentUser
from registry.core.mcp_client import clear_session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["MCP Proxy"])


@router.delete("/sessions/{server_id}")
async def clear_session_endpoint(server_id: str, user_context: CurrentUser) -> JSONResponse:
    """
    Clear/disconnect MCP session for a server (useful for debugging stale sessions).

    DELETE /api/v1/proxy/sessions/{server_id}
    """
    user_id = user_context.get("user_id", "unknown")
    session_key = f"{user_id}:{server_id}"

    clear_session(session_key)

    return JSONResponse(
        status_code=200,
        content={"success": True, "message": f"Session cleared for server {server_id}", "session_key": session_key},
    )
