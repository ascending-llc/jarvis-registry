import logging

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse

from ..schemas.mcp_registry_schema import ErrorModel, ServerListResponse, ServerResponse
from ..services import mcp_registry_service

logger = logging.getLogger(__name__)

router = APIRouter()

_PROBLEM_JSON = "application/problem+json"


def _problem(status: int, title: str, detail: str, instance: str | None = None) -> JSONResponse:
    body = ErrorModel(status=status, title=title, detail=detail, instance=instance)
    return JSONResponse(status_code=status, media_type=_PROBLEM_JSON, content=body.model_dump(exclude_none=True))


@router.get("/servers", response_model=ServerListResponse)
async def list_servers(cursor: str | None = None, limit: int | None = None) -> Response:
    try:
        result = await mcp_registry_service.list_registry_entries(cursor=cursor, limit=limit)
        return JSONResponse(content=result.model_dump(by_alias=True, exclude_none=True))
    except Exception:
        logger.exception("Failed to list MCP registry servers")
        return _problem(500, "Internal Server Error", "Internal server error")


@router.get("/servers/{full_path:path}", response_model=ServerResponse)
async def get_server(full_path: str) -> Response:
    if "/versions/" not in full_path:
        return _problem(404, "Not Found", f"Malformed server path '{full_path}'; expected <name>/versions/<version>.")
    server_name, version = full_path.rsplit("/versions/", 1)
    try:
        result = await mcp_registry_service.get_registry_entry(server_name, version)
        if result is None:
            return _problem(
                404,
                "Not Found",
                f"No server '{server_name}' at version '{version}' exists in this registry.",
            )
        return JSONResponse(content=result.model_dump(by_alias=True, exclude_none=True))
    except Exception:
        logger.exception("Failed to fetch MCP registry server '%s'", server_name)
        return _problem(500, "Internal Server Error", "Internal server error")
