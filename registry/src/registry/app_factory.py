from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.staticfiles import StaticFiles

from .core.config import settings
from .core.exception_handler import register_validation_exception_handler
from .middleware import ScopePermissionMiddleware, UnifiedAuthMiddleware
from .routers import register_routers

logger = logging.getLogger(__name__)

OPENAPI_TAGS = [
    {"name": "Authentication", "description": "OAuth2 and session-based authentication endpoints"},
    {
        "name": "Server Management",
        "description": "MCP server registration and management. Requires JWT Bearer token authentication.",
    },
    {
        "name": "A2A Agent Management V1",
        "description": "A2A agent registration and management API v1. Requires JWT Bearer token authentication.",
    },
    {
        "name": "Management API",
        "description": "IAM and user management operations. Requires JWT Bearer token with admin permissions.",
    },
    {
        "name": "Semantic Search",
        "description": "Vector-based semantic search for agents. Requires JWT Bearer token authentication.",
    },
    {"name": "Health Monitoring", "description": "Service health check endpoints"},
    {
        "name": "Anthropic Registry API",
        "description": "Anthropic-compatible registry API (v0.1) for MCP server discovery",
    },
]


def create_app(*, lifespan, gateway_mcp_app) -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="MCP Gateway Registry",
        description="A registry and management system for Model Context Protocol (MCP) servers",
        version=settings.build_version,
        lifespan=lifespan,
        swagger_ui_parameters={"persistAuthorization": True},
        generate_unique_id_function=lambda route: f"{route.tags[0]}-{route.name}" if route.tags else route.name,
        openapi_tags=OPENAPI_TAGS,
    )

    register_validation_exception_handler(app)
    _configure_middleware(app)
    _mount_static_files(app)

    # MCP app must be mounted before including any FastAPI router. This is so that requests to
    # `/proxy/mcpgw/mcp` will be routed to the MCP app instead of `proxy_router`, which has a catch-all route.
    app.mount("/proxy/mcpgw", gateway_mcp_app.streamable_http_app())
    register_routers(app)
    app.openapi = _build_openapi_factory(app)

    return app


def _configure_middleware(app: FastAPI) -> None:
    app.add_middleware(ScopePermissionMiddleware)
    app.add_middleware(UnifiedAuthMiddleware)

    # CORSMiddleware should be added late so that it executes first on incoming requests.
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"https?://(localhost(:[0-9]+)?|.*\.compute.*\.amazonaws\.com(:[0-9]+)?)",
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["Mcp-Session-Id"],
    )


def _mount_static_files(app: FastAPI) -> None:
    if hasattr(settings, "static_dir") and Path(settings.static_dir).exists():
        app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")
        logger.info("Static files mounted from %s", settings.static_dir)
    else:
        logger.warning("Static files directory not found, skipping static files mount")


def _build_openapi_factory(app: FastAPI):
    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema

        openapi_schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )

        openapi_schema["components"]["securitySchemes"] = {
            "Bearer": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
                "description": "JWT Bearer token obtained from Keycloak OAuth2 authentication. "
                "Include in Authorization header as: `Authorization: Bearer <token>`",
            }
        }

        for path, path_item in openapi_schema["paths"].items():
            if path.startswith("/api/auth/") or path == "/health" or path.startswith("/.well-known/"):
                continue

            for method in path_item:
                if method in ["get", "post", "put", "delete", "patch"] and "security" not in path_item[method]:
                    path_item[method]["security"] = [{"Bearer": []}]

        app.openapi_schema = openapi_schema
        return app.openapi_schema

    return custom_openapi
