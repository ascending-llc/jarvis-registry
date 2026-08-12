from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.testclient import TestClient
from starlette.types import Receive, Scope, Send

from registry.constants import MAX_RETURN_PATH_LENGTH
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

    @app.get("/api/v1/skills")
    async def skills_ep():
        return JSONResponse({"ok": "skills"})

    @app.get("/api/v1/skills/{skill_id}/content")
    async def skill_content_ep(skill_id: str):
        return JSONResponse({"ok": "skill-content", "skill_id": skill_id})

    @app.get("/api/v1/skills/{skill_id}")
    async def skill_detail_ep(skill_id: str):
        return JSONResponse({"ok": "skill-detail", "skill_id": skill_id})

    @app.get("/api/v1/skills/{skill_id}/files/{file_path:path}")
    async def skill_file_ep(skill_id: str, file_path: str):
        return JSONResponse({"ok": "skill-file", "skill_id": skill_id, "file_path": file_path})

    @app.post("/api/v1/skills")
    async def create_skill_ep():
        return JSONResponse({"ok": "create-skill"})

    @app.get("/proxy/a2a/test-agent")
    async def a2a_ep():
        return JSONResponse({"ok": "a2a"})

    @app.get("/proxy/server/{user_id}/{server_path:path}")
    async def direct_connect_ep(user_id: str, server_path: str):
        return JSONResponse({"ok": "direct", "user_id": user_id, "server_path": server_path})

    @app.get("/proxy/a2a/{agent_path}/agent-card.json")
    async def managed_agent_card_ep(agent_path: str):
        return JSONResponse({"ok": "managed-card", "agent_path": agent_path})

    @app.get("/proxy/a2a/{agent_path}/.well-known/agent-card.json")
    async def managed_agent_card_alias_ep(agent_path: str):
        return JSONResponse({"ok": "managed-card-alias", "agent_path": agent_path})

    @app.api_route("/proxy/a2a/{agent_path}", methods=["GET", "POST"])
    async def direct_a2a_ep(agent_path: str):
        return JSONResponse({"ok": "a2a", "agent_path": agent_path})

    @app.get("/proxy/a2a/{agent_path}/{suffix:path}")
    async def direct_a2a_suffix_ep(agent_path: str, suffix: str):
        return JSONResponse({"ok": "a2a-suffix", "agent_path": agent_path, "suffix": suffix})

    @app.get("/api/v1/servers")
    async def crud_ep():
        return JSONResponse({"ok": "crud"})

    @app.get("/api/v1/mcp/downstream/oauth/authorize/{user_id}/{server_path:path}")
    async def ds_authorize_ep(user_id: str, server_path: str):
        return JSONResponse({"ok": "authorize"})

    @app.post("/api/v1/mcp/downstream/oauth/token/{user_id}/{server_path:path}")
    async def ds_token_ep(user_id: str, server_path: str):
        return JSONResponse({"ok": "token"})

    @app.post("/api/v1/mcp/downstream/oauth/device/{user_id}/{server_path:path}")
    async def ds_device_ep(user_id: str, server_path: str):
        return JSONResponse({"ok": "device"})

    @app.get("/api/v1/mcp/consent/device/resolve")
    async def ds_device_resolve_ep():
        return JSONResponse({"ok": "resolve"})

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_build_app())


def _managed_agent_token(
    client_id: str = "mcp-client-abc",
    user_id: str | None = None,
    server_path: str | None = None,
    token_scope: str = "mcp-proxy-ops",
) -> str:
    extra: dict = {}
    if user_id is not None:
        extra["user_id"] = user_id
    if server_path is not None:
        extra["server_path"] = server_path
    return mint_managed_agent_token(
        settings.jwt_token_config,
        subject="alice",
        client_id=client_id,
        requested_scopes=token_scope,
        expires_in_seconds=3600,
        extra_claims=extra or None,
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


def test_skills_sync_401_advertises_skills_scope(client):
    resp = client.get("/api/v1/skills")

    assert resp.status_code == 401
    assert 'scope="skills-read"' in resp.headers["WWW-Authenticate"]


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/skills",
        f"/api/v1/skills/{USER_A}/content",
    ],
)
def test_skill_sync_reads_accept_managed_agent_bearer(client, path):
    token = _managed_agent_token(client_id="jarvis-registry-cli", user_id=USER_A, token_scope="skills-read")

    resp = client.get(path, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200


def test_skill_sync_read_accepts_session_cookie(client):
    client.cookies.set(_COOKIE, _crud_cookie_token())

    resp = client.get("/api/v1/skills")

    assert resp.status_code == 200


def test_skill_write_rejects_managed_agent_bearer(client):
    token = _managed_agent_token(client_id="jarvis-registry-cli", user_id=USER_A, token_scope="skills-read")

    resp = client.post("/api/v1/skills", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert "WWW-Authenticate" not in resp.headers


def test_a2a_proxy_401_advertises_a2a_scope(client):
    resp = client.get("/proxy/a2a/test-agent")

    assert resp.status_code == 401
    assert 'scope="a2a-proxy-ops"' in resp.headers["WWW-Authenticate"]


def test_direct_connect_accepts_matching_user_id(client):
    token = _managed_agent_token(user_id=USER_A, server_path="github")
    resp = client.get(f"/proxy/server/{USER_A}/github", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_direct_connect_rejects_user_id_mismatch(client):
    # User A's managed-agent token on user B's direct-connect URL must be rejected.
    token = _managed_agent_token(user_id=USER_A, server_path="github")
    resp = client.get(f"/proxy/server/{USER_B}/github", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_direct_connect_rejects_token_without_user_id(client):
    # A managed-agent token carrying no user_id claim cannot satisfy the binding.
    token = _managed_agent_token(user_id=None, server_path="github")
    resp = client.get(f"/proxy/server/{USER_A}/github", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_direct_connect_rejects_server_path_mismatch(client):
    token = _managed_agent_token(user_id=USER_A, server_path="github")
    resp = client.get(f"/proxy/server/{USER_A}/slack", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


def test_direct_connect_accepts_root_as_token_without_server_path(client):
    # Root-AS tokens (issued for requiresOAuth=False servers) carry no server_path claim.
    # The middleware should accept them on any direct-connect URL provided the user_id matches.
    token = _managed_agent_token(user_id=USER_A)
    resp = client.get(f"/proxy/server/{USER_A}/github", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_downstream_token_endpoint_is_public(client):
    # The PKCE-protected /token exchange carries no registry credential, so it must be public.
    resp = client.post(f"/api/v1/mcp/downstream/oauth/token/{USER_A}/github")
    assert resp.status_code == 200


def test_downstream_device_endpoint_is_public(client):
    resp = client.post(f"/api/v1/mcp/downstream/oauth/device/{USER_A}/github")
    assert resp.status_code == 200


def test_downstream_device_resolver_is_public(client):
    resp = client.get("/api/v1/mcp/consent/device/resolve")
    assert resp.status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "/proxy/a2a/weather/agent-card.json",
        "/proxy/a2a/weather/.well-known/agent-card.json",
    ],
)
def test_managed_agent_card_paths_are_public(client: TestClient, path: str) -> None:
    resp = client.get(path)
    assert resp.status_code == 200


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/proxy/a2a/weather"),
        ("GET", "/proxy/a2a/weather/tasks/1"),
        ("GET", "/proxy/a2a/weather/not-agent-card.json"),
    ],
)
def test_other_a2a_paths_still_require_bearer(
    client: TestClient,
    method: str,
    path: str,
) -> None:
    resp = client.request(method, path)
    assert resp.status_code == 401


def test_downstream_authorize_get_without_session_redirects_to_login(client):
    authorize_path = f"/api/v1/mcp/downstream/oauth/authorize/{USER_A}/github"
    resp = client.get(
        authorize_path,
        params={"client_id": "claude", "redirect_uri": "http://localhost:33418/cb", "state": "state-1"},
        follow_redirects=False,
    )

    assert resp.status_code == 302
    location = urlsplit(resp.headers["location"])
    assert location.path.endswith("/login")
    assert parse_qs(location.query)["next"] == [
        f"{authorize_path}?client_id=claude&redirect_uri=http%3A%2F%2Flocalhost%3A33418%2Fcb&state=state-1"
    ]


def test_downstream_authorize_non_get_without_session_still_returns_401(client):
    resp = client.post(f"/api/v1/mcp/downstream/oauth/authorize/{USER_A}/github")

    assert resp.status_code == 401


def test_downstream_authorize_oversized_return_url_is_rejected(client):
    resp = client.get(
        f"/api/v1/mcp/downstream/oauth/authorize/{USER_A}/github",
        params={"state": "x" * MAX_RETURN_PATH_LENGTH},
        follow_redirects=False,
    )

    assert resp.status_code == 414
    assert resp.json() == {"detail": "OAuth authorize return URL is too long"}
    assert "location" not in resp.headers


def test_downstream_authorize_get_with_session_reaches_route(client):
    client.cookies.set(_COOKIE, _crud_cookie_token())
    resp = client.get(f"/api/v1/mcp/downstream/oauth/authorize/{USER_A}/github")

    assert resp.status_code == 200


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


def test_lifespan_scope_passes_through_without_error():
    with TestClient(_build_app()):
        pass


@pytest.mark.parametrize(
    "path",
    [
        f"/api/v1/skills/{USER_A}",
        f"/api/v1/skills/{USER_A}/files/scripts/parse.sh",
    ],
)
def test_skill_non_sync_reads_reject_bearer(client, path):
    token = _managed_agent_token(client_id="jarvis-registry-cli", user_id=USER_A, token_scope="skills-read")

    resp = client.get(path, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert "WWW-Authenticate" not in resp.headers


@pytest.mark.parametrize(
    "path",
    [
        f"/api/v1/skills/{USER_A}",
        f"/api/v1/skills/{USER_A}/files/scripts/parse.sh",
    ],
)
def test_skill_non_sync_reads_accept_session_cookie(client, path):
    client.cookies.set(_COOKIE, _crud_cookie_token())

    resp = client.get(path)

    assert resp.status_code == 200


async def test_non_http_scope_forwarded_to_app_unchanged():
    calls: list[Scope] = []

    async def stub_app(scope: Scope, receive: Receive, send: Send) -> None:
        del receive, send
        calls.append(scope)

    middleware = UnifiedAuthMiddleware(stub_app)
    scope: Scope = {"type": "websocket", "path": "/ws"}

    await middleware(scope, AsyncMock(), AsyncMock())

    assert calls == [scope]
