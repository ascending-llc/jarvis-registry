from unittest.mock import AsyncMock

import pytest

from registry.core.config import settings
from registry.deps import get_container
from tests.conftest import make_container_factory


def _sync_response() -> dict:
    return {
        "runtime_filter_count": 0,
        "discovered": {"mcp_servers": 1, "a2a_agents": 0, "skipped_runtimes": 0},
        "created": {"mcp_servers": 1, "a2a_agents": 0},
        "updated": {"mcp_servers": 0, "a2a_agents": 0},
        "deleted": {"mcp_servers": 0, "a2a_agents": 0},
        "skipped": {"mcp_servers": 0, "a2a_agents": 0},
        "errors": [],
        "mcp_servers": [],
        "a2a_agents": [],
        "skipped_runtimes": [],
        "duration_seconds": 0.1,
    }


@pytest.mark.integration
class TestAgentCoreRuntimeSyncRoute:
    def test_sync_runtime_success(self, test_client, monkeypatch):
        mock_service = AsyncMock()
        mock_service.import_from_runtime.return_value = _sync_response()
        test_client.app.dependency_overrides[get_container] = make_container_factory(
            agentcore_import_service=mock_service
        )

        response = test_client.post(
            f"/api/{settings.api_version}/federation/agentcore/runtime/sync",
            json={"dryRun": True},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["created"]["mcp_servers"] == 1
        mock_service.import_from_runtime.assert_awaited_once()
        test_client.app.dependency_overrides.clear()

    def test_sync_runtime_maps_unexpected_error_to_500(self, test_client, monkeypatch):
        mock_service = AsyncMock()
        mock_service.import_from_runtime.side_effect = RuntimeError("boom")
        test_client.app.dependency_overrides[get_container] = make_container_factory(
            agentcore_import_service=mock_service
        )

        response = test_client.post(
            f"/api/{settings.api_version}/federation/agentcore/runtime/sync",
            json={"dryRun": False},
        )

        assert response.status_code == 500
        assert "AgentCore runtime sync failed: boom" in response.text
        test_client.app.dependency_overrides.clear()

    def test_sync_runtime_forbidden_when_rbac_denies(self, test_client, monkeypatch):
        mock_service = AsyncMock()
        mock_service.import_from_runtime.return_value = _sync_response()
        test_client.app.dependency_overrides[get_container] = make_container_factory(
            agentcore_import_service=mock_service
        )
        monkeypatch.setattr(
            "registry.middleware.rbac.ScopePermissionMiddleware._has_permission",
            lambda _self, _scopes, _path, _method: False,
        )

        response = test_client.post(
            f"/api/{settings.api_version}/federation/agentcore/runtime/sync",
            json={"dryRun": True},
        )

        assert response.status_code == 403
        assert "Insufficient permissions" in response.text
        mock_service.import_from_runtime.assert_not_awaited()
        test_client.app.dependency_overrides.clear()
