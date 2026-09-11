from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient

from registry.core.config import settings
from registry.mcp_registry_app import create_mcp_registry_app
from registry.schemas.mcp_registry_schema import (
    PaginationMetadata,
    ServerJSON,
    ServerListResponse,
    ServerResponse,
    Transport,
)

NS = settings.mcp_registry_namespace


def _sample_entry() -> ServerResponse:
    return ServerResponse(
        server=ServerJSON(
            name=f"{NS}/github",
            title="github",
            description="GitHub tools",
            version="1.2.3",
            remotes=[Transport(type="streamable-http", url="https://x/proxy/server/{userId}/mcp/github")],
        )
    )


@pytest.fixture
def client():
    return TestClient(create_mcp_registry_app())


def test_list_servers_returns_200_and_shape(client, monkeypatch):
    payload = ServerListResponse(servers=[_sample_entry()], metadata=PaginationMetadata(count=1, nextCursor=None))
    monkeypatch.setattr(
        "registry.api.mcp_registry_routes.mcp_registry_service.list_registry_entries",
        AsyncMock(return_value=payload),
    )
    resp = client.get("/servers")
    assert resp.status_code == 200
    body = resp.json()
    assert body["metadata"]["count"] == 1
    assert body["servers"][0]["server"]["name"] == f"{NS}/github"
    # {userId} placeholder must survive JSON serialization untouched
    assert "{userId}" in body["servers"][0]["server"]["remotes"][0]["url"]


def test_get_server_returns_200(client, monkeypatch):
    monkeypatch.setattr(
        "registry.api.mcp_registry_routes.mcp_registry_service.get_registry_entry",
        AsyncMock(return_value=_sample_entry()),
    )
    resp = client.get(f"/servers/{NS}%2Fgithub/versions/latest")
    assert resp.status_code == 200
    assert resp.json()["server"]["name"] == f"{NS}/github"


def test_get_unknown_server_returns_rfc7807_404(client, monkeypatch):
    monkeypatch.setattr(
        "registry.api.mcp_registry_routes.mcp_registry_service.get_registry_entry",
        AsyncMock(return_value=None),
    )
    resp = client.get(f"/servers/{NS}%2Fnope/versions/latest")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert body["status"] == 404
    assert body["title"] == "Not Found"
    assert "detail" in body


def test_get_malformed_path_returns_404(client):
    resp = client.get("/servers/no-version-segment")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/problem+json")


def test_list_server_error_returns_rfc7807_500(client, monkeypatch):
    monkeypatch.setattr(
        "registry.api.mcp_registry_routes.mcp_registry_service.list_registry_entries",
        AsyncMock(side_effect=RuntimeError("boom")),
    )
    resp = client.get("/servers")
    assert resp.status_code == 500
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.json()["status"] == 500
