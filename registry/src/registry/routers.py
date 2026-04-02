from fastapi import FastAPI

from .api.management_routes import router as management_router
from .api.proxy_routes import router as proxy_router
from .api.redirect_routes import router as auth_provider_router
from .api.system_routes import router as system_router
from .api.v1.a2a.agent_routes import router as a2a_agent_router
from .api.v1.acl_routes import router as acl_router
from .api.v1.federation.agentcore_routes import router as agentcore_federation_router
from .api.v1.federation.federation_routes import router as federation_router
from .api.v1.mcp.connection_router import router as connection_router
from .api.v1.mcp.oauth_router import router as oauth_router
from .api.v1.meta_routes import router as meta_router
from .api.v1.search_routes import router as search_router
from .api.v1.server.server_routes import router as servers_router_v1
from .api.v1.token_routes import router as token_router
from .api.wellknown_routes import router as wellknown_router
from .core.config import settings
from .health.routes import router as health_router


def register_routers(app: FastAPI) -> None:
    """Register all HTTP routers for the registry application."""
    app.include_router(meta_router, prefix="/api/auth", tags=["Authentication metadata"])
    app.include_router(token_router, prefix=f"/api/{settings.api_version}", tags=["Server Management"])
    app.include_router(servers_router_v1, prefix=f"/api/{settings.api_version}", tags=["Server Management V1"])
    app.include_router(a2a_agent_router, prefix=f"/api/{settings.api_version}", tags=["A2A Agent Management V1"])
    app.include_router(management_router, prefix="/api")
    app.include_router(search_router, prefix=f"/api/{settings.api_version}", tags=["Semantic Search"])
    app.include_router(health_router, prefix="/api/health", tags=["Health Monitoring"])
    app.include_router(oauth_router, prefix=f"/api/{settings.api_version}", tags=["MCP  Oauth Management"])
    app.include_router(connection_router, prefix=f"/api/{settings.api_version}", tags=["MCP  Connection Management"])
    app.include_router(acl_router, prefix=f"/api/{settings.api_version}", tags=["ACL Management"])
    app.include_router(
        agentcore_federation_router,
        prefix=f"/api/{settings.api_version}",
        tags=["AgentCore Management"],
    )
    app.include_router(
        federation_router,
        prefix=f"/api/{settings.api_version}",
        tags=["Federation Management"],
    )
    app.include_router(system_router)
    app.include_router(auth_provider_router, tags=["Authentication"])
    app.include_router(proxy_router, prefix="/proxy", tags=["MCP Proxy"])
    app.include_router(wellknown_router, prefix="/.well-known", tags=["Discovery"])
