from types import SimpleNamespace
from unittest.mock import MagicMock

from registry.services.search.external_service import ExternalVectorSearchService


def _service() -> ExternalVectorSearchService:
    return ExternalVectorSearchService(mcp_server_repo=MagicMock())


def _fake_server(*, enabled: bool):
    return SimpleNamespace(
        serverName="test-server",
        path="/test",
        tags=["t"],
        numTools=2,
        numStars=0,
        config={"enabled": enabled, "title": "Test", "description": "desc"},
    )


def test_servers_to_results_is_enabled_from_config_enabled():
    service = _service()

    results = service._servers_to_results([_fake_server(enabled=True)])

    assert len(results) == 1
    assert results[0]["is_enabled"] is True
    # status is hard-coded; never reflects the deprecated doc field
    assert results[0]["status"] == "active"


def test_servers_to_results_disabled_when_config_enabled_false():
    service = _service()

    results = service._servers_to_results([_fake_server(enabled=False)])

    assert results[0]["is_enabled"] is False
    assert results[0]["status"] == "active"


def test_servers_to_results_disabled_when_enabled_missing():
    service = _service()
    server = SimpleNamespace(serverName="s", path="/s", tags=[], numTools=0, numStars=0, config={"title": "t"})

    results = service._servers_to_results([server])

    assert results[0]["is_enabled"] is False
