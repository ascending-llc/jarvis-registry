import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.testclient import TestClient

from registry.core.config import settings
from registry.middleware.auth import UnifiedAuthMiddleware
from registry.utils.crypto_utils import generate_access_token
from registry_pkgs.core.jwt_tokens import mint_managed_agent_token

_COOKIE = settings.session_cookie_name


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(UnifiedAuthMiddleware)

    @app.get("/proxy/mcpgw/mcp")
    async def proxy_ep():  # pragma: no cover - body trivial
        return JSONResponse({"ok": "proxy"})

    @app.get("/api/v1/servers")
    async def crud_ep():  # pragma: no cover - body trivial
        return JSONResponse({"ok": "crud"})

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_build_app())


def _managed_agent_token(client_id: str = "mcp-client-abc") -> str:
    return mint_managed_agent_token(
        settings.jwt_token_config,
        subject="alice",
        client_id=client_id,
        expires_in_seconds=3600,
        extra_claims={"scope": "mcp-proxy-ops"},
    )


def _crud_cookie_token() -> str:
    return generate_access_token(
        user_id="u1",
        username="alice",
        email="alice@example.com",
        groups=["g1"],
        scopes=["servers-read"],
        role="user",
        auth_method="oauth2",
        provider="entra",
    )


def test_proxy_accepts_managed_agent_bearer(client):
    resp = client.get("/proxy/mcpgw/mcp", headers={"Authorization": f"Bearer {_managed_agent_token()}"})
    assert resp.status_code == 200


def test_proxy_rejects_crud_token_as_bearer(client):
    # CRUD-session token presented as a Bearer must not work on proxy routes.
    resp = client.get("/proxy/mcpgw/mcp", headers={"Authorization": f"Bearer {_crud_cookie_token()}"})
    assert resp.status_code == 401


def test_proxy_ignores_cookie(client):
    # A valid CRUD cookie must not authenticate a proxy route.
    client.cookies.set(_COOKIE, _crud_cookie_token())
    resp = client.get("/proxy/mcpgw/mcp")
    assert resp.status_code == 401


def test_proxy_rejects_registry_client_id_token(client):
    token = _managed_agent_token(client_id=settings.registry_app_name)
    resp = client.get("/proxy/mcpgw/mcp", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_proxy_rejects_non_bearer_scheme(client):
    resp = client.get("/proxy/mcpgw/mcp", headers={"Authorization": f"Basic {_managed_agent_token()}"})
    assert resp.status_code == 401


def test_proxy_accepts_bearer_scheme_case_insensitively(client):
    resp = client.get("/proxy/mcpgw/mcp", headers={"Authorization": f"bearer {_managed_agent_token()}"})
    assert resp.status_code == 200


def test_crud_accepts_session_cookie(client):
    client.cookies.set(_COOKIE, _crud_cookie_token())
    resp = client.get("/api/v1/servers")
    assert resp.status_code == 200


def test_crud_rejects_managed_agent_token_in_cookie(client):
    # Epic regression: leaked managed-agent token replayed as session cookie must fail.
    client.cookies.set(_COOKIE, _managed_agent_token())
    resp = client.get("/api/v1/servers")
    assert resp.status_code == 401


def test_crud_ignores_bearer_header(client):
    resp = client.get("/api/v1/servers", headers={"Authorization": f"Bearer {_managed_agent_token()}"})
    assert resp.status_code == 401


def test_crud_401_advertises_no_bearer_challenge(client):
    resp = client.get("/api/v1/servers")
    assert resp.status_code == 401
    assert "WWW-Authenticate" not in resp.headers


def test_proxy_401_advertises_bearer_challenge(client):
    resp = client.get("/proxy/mcpgw/mcp")
    assert resp.status_code == 401
    assert resp.headers.get("WWW-Authenticate", "").startswith("Bearer")


def test_all_proxy_router_paths_classify_as_proxy():
    from registry.api.proxy_routes import router as proxy_router

    mw = UnifiedAuthMiddleware(FastAPI())
    proxy_paths = [r.path for r in proxy_router.routes if getattr(r, "path", None) is not None]
    assert proxy_paths, "expected proxy_router to expose routes"
    for path in proxy_paths:
        assert mw._is_proxy_route(f"/proxy{path}") is True


@pytest.mark.parametrize(
    "path",
    ["/api/v1/servers", "/api/v1/agents", "/api/auth/me", "/api/v1/tokens/generate", "/api/health/status"],
)
def test_crud_paths_classify_as_non_proxy(path):
    mw = UnifiedAuthMiddleware(FastAPI())
    assert mw._is_proxy_route(path) is False
