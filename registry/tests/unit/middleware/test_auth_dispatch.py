import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.testclient import TestClient

from registry.auth.downstream_token import mint_downstream_mcp_token
from registry.core.config import settings
from registry.middleware.auth import UnifiedAuthMiddleware
from registry.utils.crypto_utils import generate_access_token
from registry_pkgs.core.jwt_tokens import mint_managed_agent_token

_COOKIE = settings.session_cookie_name

USER_A = "507f1f77bcf86cd799439011"
USER_B = "507f1f77bcf86cd799439012"


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(UnifiedAuthMiddleware)

    @app.get("/proxy/mcpgw/mcp")
    async def proxy_ep():  # pragma: no cover - body trivial
        return JSONResponse({"ok": "proxy"})

    @app.get("/proxy/server/{user_id}/{server_path:path}")
    async def direct_connect_ep(user_id: str, server_path: str):
        return JSONResponse({"ok": "direct", "user_id": user_id, "server_path": server_path})

    @app.get("/api/v1/servers")
    async def crud_ep():
        return JSONResponse({"ok": "crud"})

    @app.get("/api/v1/mcp/downstream/oauth/authorize/{user_id}/{server_path:path}")
    async def ds_authorize_ep(user_id: str, server_path: str):
        return JSONResponse({"ok": "authorize"})

    @app.post("/api/v1/mcp/downstream/oauth/token/{user_id}/{server_path:path}")
    async def ds_token_ep(user_id: str, server_path: str):
        return JSONResponse({"ok": "token"})

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_build_app())


def _managed_agent_token(client_id: str = "mcp-client-abc", user_id: str | None = None) -> str:
    extra: dict = {"scope": "mcp-proxy-ops"}
    if user_id is not None:
        extra["user_id"] = user_id
    return mint_managed_agent_token(
        settings.jwt_token_config,
        subject="alice",
        client_id=client_id,
        expires_in_seconds=3600,
        extra_claims=extra,
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


def _downstream_token(user_id: str = USER_A, server_path: str = "github") -> str:
    return mint_downstream_mcp_token(settings.jwt_token_config, user_id=user_id, server_path=server_path)


def test_direct_connect_accepts_matching_user_id(client):
    token = _managed_agent_token(user_id=USER_A)
    resp = client.get(f"/proxy/server/{USER_A}/github", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_direct_connect_rejects_user_id_mismatch(client):
    # User A's managed-agent token on user B's direct-connect URL must be rejected.
    token = _managed_agent_token(user_id=USER_A)
    resp = client.get(f"/proxy/server/{USER_B}/github", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_direct_connect_rejects_token_without_user_id(client):
    # A managed-agent token carrying no user_id claim cannot satisfy the binding.
    token = _managed_agent_token(user_id=None)
    resp = client.get(f"/proxy/server/{USER_A}/github", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_direct_connect_accepts_downstream_confirmation_token(client):
    token = _downstream_token(user_id=USER_A, server_path="github")
    resp = client.get(f"/proxy/server/{USER_A}/github", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_downstream_token_rejected_cross_user(client):
    token = _downstream_token(user_id=USER_A, server_path="github")
    resp = client.get(f"/proxy/server/{USER_B}/github", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_downstream_token_rejected_cross_server(client):
    token = _downstream_token(user_id=USER_A, server_path="github")
    resp = client.get(f"/proxy/server/{USER_A}/slack", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_downstream_token_endpoint_is_public(client):
    # The PKCE-protected /token exchange carries no registry credential, so it must be public.
    resp = client.post(f"/api/v1/mcp/downstream/oauth/token/{USER_A}/github")
    assert resp.status_code == 200


def test_downstream_authorize_endpoint_is_not_public(client):
    resp = client.get(f"/api/v1/mcp/downstream/oauth/authorize/{USER_A}/github")
    assert resp.status_code == 401


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
