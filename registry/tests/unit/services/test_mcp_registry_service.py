from unittest.mock import AsyncMock

import pytest

from registry.core.config import Settings
from registry.services import mcp_registry_service as svc

NS = "com.ascendingdc.jarvis"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        registry_url="https://demo.example.com/gateway",
        registry_client_url="https://demo.example.com/gateway",
        build_version="1.2.3",
        mcp_registry_namespace=NS,
        mcp_registry_default_limit=30,
        mcp_registry_max_limit=100,
    )


class _FakeQuery:
    def __init__(self, items):
        self._items = items

    async def to_list(self):
        return list(self._items)


def _server(server_name, path="/p", tags=None, enabled=True, type_="streamable-http", description="desc"):
    from registry_pkgs.models import ExtendedMCPServer

    return ExtendedMCPServer.model_construct(
        serverName=server_name,
        path=path,
        tags=tags or [],
        config={"enabled": enabled, "type": type_, "description": description},
    )


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------


def test_discover_execute_entry_points_at_gateway(settings):
    entry = svc.build_discover_execute_entry(settings)
    assert entry.server.name == f"{NS}/discover-execute"
    assert entry.server.remotes[0].url == "https://demo.example.com/gateway/proxy/mcpgw/mcp"
    assert entry.server.remotes[0].variables is None  # not per-user


def test_server_entry_url_variables_and_title(settings):
    entry = svc.build_server_entry(_server("github", path="/mcp/github", tags=["git"]), settings)
    remote = entry.server.remotes[0]
    assert entry.server.name == f"{NS}/github"
    assert entry.server.title == "github"  # human-readable label, not the namespaced name
    # {userId} placeholder preserved verbatim; leading slash comes from server.path; /proxy prefix present
    assert remote.url == "https://demo.example.com/gateway/proxy/server/{userId}/mcp/github"
    assert remote.variables["userId"].isRequired is True


def test_server_entry_meta_never_leaks_internal_fields(settings):
    entry = svc.build_server_entry(_server("github", path="/mcp/github", tags=["git"]), settings)
    internal = entry.server.meta[f"{NS}/internal"]
    assert internal == {"path": "/mcp/github", "tags": ["git"]}
    dumped = str(entry.model_dump(by_alias=True))
    for forbidden in ("lastConnected", "lastError", "errorMessage"):
        assert forbidden not in dumped


def test_description_empty_falls_back_to_server_name(settings):
    assert svc.build_server_entry(_server("jira", description=None), settings).server.description == "jira"
    assert svc.build_server_entry(_server("jira", description=""), settings).server.description == "jira"


def test_description_truncated_to_100_chars(settings):
    entry = svc.build_server_entry(_server("big", description="z" * 250), settings)
    assert len(entry.server.description) == 100


# ---------------------------------------------------------------------------
# list: eligibility, static entry, pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_applies_eligibility_filter(settings, monkeypatch):
    captured = {}

    def fake_find(filt):
        captured["filter"] = filt
        return _FakeQuery([])

    monkeypatch.setattr(svc.ExtendedMCPServer, "find", fake_find)
    await svc.list_registry_entries(settings)
    assert captured["filter"] == {
        "config.enabled": True,
        "config.type": "streamable-http",
        "path": {"$type": "string"},
    }


@pytest.mark.asyncio
async def test_list_always_includes_static_entry_even_with_no_servers(settings, monkeypatch):
    monkeypatch.setattr(svc.ExtendedMCPServer, "find", lambda filt: _FakeQuery([]))
    result = await svc.list_registry_entries(settings)
    assert [e.server.name for e in result.servers] == [f"{NS}/discover-execute"]
    assert result.metadata.count == 1
    assert result.metadata.nextCursor is None


@pytest.mark.asyncio
async def test_list_pagination_default_clamp_and_cursor(settings, monkeypatch):
    servers = [_server(f"s{i:02d}", path=f"/s{i}") for i in range(10)]
    monkeypatch.setattr(svc.ExtendedMCPServer, "find", lambda filt: _FakeQuery(servers))

    # limit clamped to >=1; sorted by name; first page yields a cursor
    page1 = await svc.list_registry_entries(settings, limit=4)
    assert page1.metadata.count == 4
    names1 = [e.server.name for e in page1.servers]
    assert names1 == sorted(names1)
    assert page1.metadata.nextCursor == names1[-1]

    # continue after the cursor
    page2 = await svc.list_registry_entries(settings, cursor=page1.metadata.nextCursor, limit=4)
    assert page2.servers[0].server.name > names1[-1]

    # limit above max is clamped
    big = await svc.list_registry_entries(settings, limit=9999)
    assert big.metadata.count == 11  # 10 servers + static; all fit under clamp(100)
    assert big.metadata.nextCursor is None


def test_empty_build_version_falls_back_not_crashes(monkeypatch):
    # BUILD_VERSION is injected empty in some deployments; server.json requires version >= 1 char.
    s = Settings(
        registry_url="https://d/gateway",
        registry_client_url="https://d/gateway",
        build_version="",
        mcp_registry_namespace=NS,
    )
    static = svc.build_discover_execute_entry(s)  # must not raise
    assert static.server.version == "0.0.0"
    entry = svc.build_server_entry(_server("github", path="/mcp/github"), s)
    assert entry.server.version == "0.0.0"


@pytest.mark.asyncio
async def test_list_skips_malformed_server_name_instead_of_crashing(settings, monkeypatch):
    # A serverName outside server.json's `name` charset must be excluded, not 500 the whole list.
    good = _server("github", path="/mcp/github")
    bad = _server("weird name!$", path="/b")
    monkeypatch.setattr(svc.ExtendedMCPServer, "find", lambda filt: _FakeQuery([good, bad]))
    result = await svc.list_registry_entries(settings)
    names = [e.server.name for e in result.servers]
    assert f"{NS}/github" in names
    assert all("weird" not in n for n in names)  # bad one dropped
    assert result.metadata.count == 2  # static + github, not 3


@pytest.mark.asyncio
async def test_get_malformed_server_name_returns_none_not_error(settings, monkeypatch):
    monkeypatch.setattr(svc.ExtendedMCPServer, "find_one", AsyncMock(return_value=_server("bad name!$")))
    assert await svc.get_registry_entry(f"{NS}/bad name!$", "latest", settings) is None


@pytest.mark.asyncio
async def test_list_limit_below_one_clamped_to_one(settings, monkeypatch):
    monkeypatch.setattr(svc.ExtendedMCPServer, "find", lambda filt: _FakeQuery([]))
    result = await svc.list_registry_entries(settings, limit=0)
    assert result.metadata.count == 1  # static entry, clamp floor of 1


# ---------------------------------------------------------------------------
# get: version handling and lookup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_static_entry_needs_no_db(settings, monkeypatch):
    boom = AsyncMock(side_effect=AssertionError("must not query DB for static entry"))
    monkeypatch.setattr(svc.ExtendedMCPServer, "find_one", boom)
    entry = await svc.get_registry_entry(f"{NS}/discover-execute", "latest", settings)
    assert entry is not None
    assert entry.server.name == f"{NS}/discover-execute"


@pytest.mark.asyncio
async def test_get_resolves_latest_and_exact_version(settings, monkeypatch):
    monkeypatch.setattr(
        svc.ExtendedMCPServer, "find_one", AsyncMock(return_value=_server("github", path="/mcp/github"))
    )
    for version in ("latest", "1.2.3"):
        entry = await svc.get_registry_entry(f"{NS}/github", version, settings)
        assert entry is not None and entry.server.name == f"{NS}/github"


@pytest.mark.asyncio
async def test_get_unknown_version_returns_none(settings, monkeypatch):
    monkeypatch.setattr(svc.ExtendedMCPServer, "find_one", AsyncMock(return_value=_server("github")))
    assert await svc.get_registry_entry(f"{NS}/github", "9.9.9", settings) is None


@pytest.mark.asyncio
async def test_get_unknown_name_returns_none(settings, monkeypatch):
    monkeypatch.setattr(svc.ExtendedMCPServer, "find_one", AsyncMock(return_value=None))
    assert await svc.get_registry_entry(f"{NS}/nope", "latest", settings) is None


@pytest.mark.asyncio
async def test_get_wrong_namespace_prefix_returns_none(settings, monkeypatch):
    monkeypatch.setattr(svc.ExtendedMCPServer, "find_one", AsyncMock(side_effect=AssertionError("no DB")))
    assert await svc.get_registry_entry("io.other/github", "latest", settings) is None


@pytest.mark.asyncio
async def test_get_strips_namespace_before_db_lookup(settings, monkeypatch):
    captured = {}

    async def fake_find_one(filt):
        captured["filter"] = filt
        return None

    monkeypatch.setattr(svc.ExtendedMCPServer, "find_one", fake_find_one)
    await svc.get_registry_entry(f"{NS}/github", "latest", settings)
    assert captured["filter"]["serverName"] == "github"
    assert captured["filter"]["config.enabled"] is True
