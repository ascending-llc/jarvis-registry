from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.routing import Mount

from .core.config import settings
from .main import app as main_app
from .main import lifespan as main_lifespan
from .mcp_registry_app import create_mcp_registry_app


@asynccontextmanager
async def _lifespan(_dispatcher_app: Starlette):
    async with main_lifespan(main_app):
        yield


def build_dispatcher() -> Starlette:
    """Compose the dispatcher, gating the ``/v0.1`` mount on ``enable_mcp_registry``."""
    routes = [Mount("/", app=main_app)]
    if settings.enable_mcp_registry:
        routes = [Mount("/v0.1", app=create_mcp_registry_app())] + routes
    return Starlette(routes=routes, lifespan=_lifespan)


app = build_dispatcher()
