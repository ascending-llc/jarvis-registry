from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient

from registry.core.config import settings
from registry.schemas.mcp_registry_schema import PaginationMetadata, ServerListResponse


@pytest.fixture
def enabled_dispatcher(monkeypatch):
    """Build a dispatcher with the feature flag on and the catalog service stubbed.

    Uses TestClient WITHOUT a context manager so the (real Mongo/Redis) lifespan does not run.
    """
    monkeypatch.setattr(settings, "enable_mcp_registry", True)
    monkeypatch.setattr(
        "registry.api.mcp_registry_routes.mcp_registry_service.list_registry_entries",
        AsyncMock(return_value=ServerListResponse(servers=[], metadata=PaginationMetadata(count=0, nextCursor=None))),
    )
    from registry.dispatcher import build_dispatcher

    return build_dispatcher()


def test_v0_1_is_anonymous(enabled_dispatcher):
    # No Authorization header, no cookies -> still 200 (request never enters UnifiedAuthMiddleware).
    resp = TestClient(enabled_dispatcher).get("/v0.1/servers")
    assert resp.status_code == 200


def test_v0_1_preflight_allows_any_origin(enabled_dispatcher):
    resp = TestClient(enabled_dispatcher).options(
        "/v0.1/servers",
        headers={"Origin": "https://anything.example", "Access-Control-Request-Method": "GET"},
    )
    assert resp.headers.get("access-control-allow-origin") == "*"


def test_main_app_route_does_not_allow_any_origin(enabled_dispatcher):
    # The same preflight against an existing main_app route must NOT echo a wildcard origin.
    resp = TestClient(enabled_dispatcher).options(
        "/health",
        headers={"Origin": "https://anything.example", "Access-Control-Request-Method": "GET"},
    )
    assert resp.headers.get("access-control-allow-origin") != "*"


def test_flag_off_removes_the_mount(monkeypatch):
    monkeypatch.setattr(settings, "enable_mcp_registry", False)
    from registry.dispatcher import build_dispatcher

    resp = TestClient(build_dispatcher()).get("/v0.1/servers")
    # The anonymous catalog mount is absent: the request falls through to main_app, whose auth
    # middleware handles the unknown path (401), never our 200 catalog. (Auth runs before the
    # catch-all, so it's 401 rather than a bare 404 — either way the anonymous surface is gone.)
    assert resp.status_code != 200
    assert resp.status_code in (401, 404)


def test_root_path_resolves_v0_1_mount(enabled_dispatcher):
    # Simulate uvicorn --root-path /gateway (production NGINX_BASE_PATH): the new /v0.1 mount
    # (one Mount deep under the dispatcher) still routes to a 200.
    client = TestClient(enabled_dispatcher, root_path="/gateway")
    assert client.get("/gateway/v0.1/servers").status_code == 200


def test_root_path_resolves_nested_fastmcp_style_mount():
    """Guard the exact nesting the dispatcher adds around the pre-existing FastMCP mount.

    Reproduces dispatcher -> Mount('/') main -> Mount('/proxy/mcpgw') inner and asserts that under
    a non-empty root_path the innermost app is reached and Starlette strips the accumulated prefix
    so ``get_route_path`` yields the app's own sub-path (``/mcp``). The live FastMCP mount can't be
    probed here (its session manager isn't running and the main app's middleware stack intercepts),
    so this isolates the routing mechanism that the entrypoint's --root-path comment depends on.
    """
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route

    seen = {}

    async def inner(scope, receive, send):
        # What get_route_path() computes: scope["path"] with the accumulated root_path stripped.
        seen["route_path"] = scope["path"][len(scope.get("root_path", "")) :]
        await JSONResponse({"ok": True})(scope, receive, send)

    main = Starlette(routes=[Mount("/proxy/mcpgw", app=inner)])
    disp = Starlette(
        routes=[Mount("/v0.1", routes=[Route("/servers", lambda r: JSONResponse({}))]), Mount("/", app=main)]
    )

    resp = TestClient(disp, root_path="/gateway").get("/gateway/proxy/mcpgw/mcp")
    assert resp.status_code == 200
    assert seen["route_path"] == "/mcp"  # accumulated /gateway/proxy/mcpgw prefix correctly stripped
