from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from .api.mcp_registry_routes import router as mcp_registry_router


def create_mcp_registry_app() -> FastAPI:
    app = FastAPI(title="Jarvis MCP Registry", openapi_url=None)
    app.include_router(mcp_registry_router)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )
    return app
