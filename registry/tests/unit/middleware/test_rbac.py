"""
Unit tests for RBAC middleware (registry/src/registry/middleware/rbac.py).

Tests cover:
- Path normalization (_normalize_path)
- Rule specificity sorting
- Rule matching logic
- Permission checking (first match wins)
- Integration scenarios with FastAPI
"""

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from registry.middleware.rbac import (
    ScopePermissionMiddleware,
    _normalize_endpoint_pattern,
    _normalize_path,
    _parse_methods,
)

# Store the original _has_permission method at module level
_original_has_permission = ScopePermissionMiddleware._has_permission


@pytest.fixture(autouse=True)
def restore_rbac_for_rbac_tests(monkeypatch):
    """Restore original RBAC behavior for these specific tests."""
    # Restore the original _has_permission method
    monkeypatch.setattr(ScopePermissionMiddleware, "_has_permission", _original_has_permission)
    yield


@pytest.mark.unit
class TestPathNormalization:
    """Test path normalization logic."""

    def test_strips_api_v1_prefix(self, monkeypatch):
        """Strips /api/v1 prefix from paths."""
        from registry.core import config as config_module

        monkeypatch.setattr(config_module.settings, "API_VERSION", "v1")
        assert _normalize_path("/api/v1/servers") == "/servers"

    def test_strips_api_prefix(self, monkeypatch):
        """Strips /api prefix from paths."""
        from registry.core import config as config_module

        monkeypatch.setattr(config_module.settings, "API_VERSION", "v1")
        assert _normalize_path("/api/servers") == "/servers"

    def test_returns_original_without_prefix(self, monkeypatch):
        """Returns original path when no prefix to strip."""
        from registry.core import config as config_module

        monkeypatch.setattr(config_module.settings, "API_VERSION", "v1")
        assert _normalize_path("/servers") == "/servers"

    def test_strips_api_v2_prefix(self, monkeypatch):
        """Strips /api/v2 prefix when API_VERSION is v2."""
        from registry.core import config as config_module

        monkeypatch.setattr(config_module.settings, "API_VERSION", "v2")
        assert _normalize_path("/api/v2/agents") == "/agents"

    def test_nested_path_with_api_v1(self, monkeypatch):
        """Handles nested paths with /api/v1 prefix."""
        from registry.core import config as config_module

        monkeypatch.setattr(config_module.settings, "API_VERSION", "v1")
        assert _normalize_path("/api/v1/auth/me") == "/auth/me"

    def test_path_with_id_parameter(self, monkeypatch):
        """Handles paths with ID parameters."""
        from registry.core import config as config_module

        monkeypatch.setattr(config_module.settings, "API_VERSION", "v1")
        assert _normalize_path("/api/v1/servers/abc123") == "/servers/abc123"


@pytest.mark.unit
class TestEndpointPatternNormalization:
    """Test endpoint pattern normalization for compilation."""

    def test_converts_path_wildcard(self):
        """Converts {path} to {path:path} for slash support."""
        assert _normalize_endpoint_pattern("/agents/{path}") == "/agents/{path:path}"

    def test_strips_whitespace(self):
        """Strips leading/trailing whitespace."""
        assert _normalize_endpoint_pattern("  /servers  ") == "/servers"

    def test_handles_mixed_params(self):
        """Handles endpoints with both regular and path params."""
        result = _normalize_endpoint_pattern("/servers/{server_id}/agents/{path}")
        assert result == "/servers/{server_id}/agents/{path:path}"


@pytest.mark.unit
class TestMethodParsing:
    """Test HTTP method parsing from scopes.yml."""

    def test_wildcard_returns_none(self):
        """Wildcard * returns None (matches all methods)."""
        assert _parse_methods("*") is None

    def test_single_method(self):
        """Parses single method to set."""
        assert _parse_methods("GET") == {"GET"}

    def test_comma_separated_methods(self):
        """Parses comma-separated methods to set."""
        assert _parse_methods("GET,POST,PUT") == {"GET", "POST", "PUT"}

    def test_case_normalization(self):
        """Normalizes methods to uppercase."""
        assert _parse_methods("get,post") == {"GET", "POST"}

    def test_strips_whitespace(self):
        """Strips whitespace around methods."""
        assert _parse_methods(" GET , POST ") == {"GET", "POST"}

    def test_empty_string(self):
        """Returns empty set for empty string."""
        assert _parse_methods("") == set()


@pytest.mark.unit
class TestRuleSpecificity:
    """Test rule specificity sorting logic."""

    def test_static_path_before_parameterized(self, monkeypatch):
        """Static paths rank higher than parameterized paths."""
        from registry.middleware import rbac as rbac_module

        mock_settings = MagicMock()
        mock_settings.API_VERSION = "v1"
        mock_settings.scopes_config = {
            "test-scope": [
                {"endpoint": "/servers/{server_id}", "method": "GET"},
                {"endpoint": "/servers/stats", "method": "GET"},
            ]
        }
        monkeypatch.setattr(rbac_module, "settings", mock_settings)

        middleware = ScopePermissionMiddleware(app=MagicMock())
        # After sorting, /servers/stats should come before /servers/{server_id}
        assert middleware._rules[0]["endpoint"] == "/servers/stats"
        assert middleware._rules[1]["endpoint"] == "/servers/{server_id}"

    def test_fewer_params_before_more_params(self, monkeypatch):
        """Endpoints with fewer params rank higher."""
        from registry.middleware import rbac as rbac_module

        mock_settings = MagicMock()
        mock_settings.API_VERSION = "v1"
        mock_settings.scopes_config = {
            "test-scope": [
                {"endpoint": "/a/{x}/{y}/{z}", "method": "GET"},
                {"endpoint": "/a/{x}", "method": "GET"},
                {"endpoint": "/a/{x}/{y}", "method": "GET"},
            ]
        }
        monkeypatch.setattr(rbac_module, "settings", mock_settings)

        middleware = ScopePermissionMiddleware(app=MagicMock())
        # Should sort by param count: 1 param, 2 params, 3 params
        param_counts = [rule["endpoint"].count("{") for rule in middleware._rules]
        assert param_counts == [1, 2, 3]

    def test_longer_path_before_shorter(self, monkeypatch):
        """For same param count, longer paths (more segments) rank higher."""
        from registry.middleware import rbac as rbac_module

        mock_settings = MagicMock()
        mock_settings.API_VERSION = "v1"
        mock_settings.scopes_config = {
            "test-scope": [
                {"endpoint": "/a", "method": "GET"},
                {"endpoint": "/a/b/c", "method": "GET"},
                {"endpoint": "/a/b", "method": "GET"},
            ]
        }
        monkeypatch.setattr(rbac_module, "settings", mock_settings)

        middleware = ScopePermissionMiddleware(app=MagicMock())
        # Should sort by segment count descending: /a/b/c, /a/b, /a
        endpoints = [rule["endpoint"] for rule in middleware._rules]
        assert endpoints == ["/a/b/c", "/a/b", "/a"]


@pytest.mark.unit
class TestPermissionChecking:
    """Test permission checking logic (first match wins)."""

    def test_first_match_wins_allows_access(self, monkeypatch):
        """When first matching rule grants access, allows request."""
        from registry.middleware import rbac as rbac_module

        mock_settings = MagicMock()
        mock_settings.API_VERSION = "v1"
        mock_settings.scopes_config = {
            "system-ops": [{"endpoint": "/servers/stats", "method": "GET"}],
            "servers-read": [{"endpoint": "/servers/{server_id}", "method": "GET"}],
        }
        monkeypatch.setattr(rbac_module, "settings", mock_settings)

        middleware = ScopePermissionMiddleware(app=MagicMock())
        # User has system-ops scope
        result = middleware._has_permission(["system-ops"], "/servers/stats", "GET")
        assert result is True

    def test_first_match_wins_denies_access(self, monkeypatch):
        """When first matching rule denies access, denies request."""
        from registry.middleware import rbac as rbac_module

        mock_settings = MagicMock()
        mock_settings.API_VERSION = "v1"
        mock_settings.scopes_config = {
            "system-ops": [{"endpoint": "/servers/stats", "method": "GET"}],
            "servers-read": [{"endpoint": "/servers/{server_id}", "method": "GET"}],
        }
        monkeypatch.setattr(rbac_module, "settings", mock_settings)

        middleware = ScopePermissionMiddleware(app=MagicMock())
        # User only has servers-read scope, not system-ops
        result = middleware._has_permission(["servers-read"], "/servers/stats", "GET")
        assert result is False

    def test_stops_at_first_match_does_not_check_later_rules(self, monkeypatch):
        """Does not check subsequent rules after first match."""
        from registry.middleware import rbac as rbac_module

        mock_settings = MagicMock()
        mock_settings.API_VERSION = "v1"
        mock_settings.scopes_config = {
            "system-ops": [{"endpoint": "/servers/stats", "method": "GET"}],
            "servers-read": [{"endpoint": "/servers/{server_id}", "method": "GET"}],
        }
        monkeypatch.setattr(rbac_module, "settings", mock_settings)

        middleware = ScopePermissionMiddleware(app=MagicMock())
        # /servers/stats matches system-ops first (more specific)
        # Even though user has servers-read which could match via {server_id}
        # it should stop at first match and deny
        result = middleware._has_permission(["servers-read"], "/servers/stats", "GET")
        assert result is False

    def test_parameterized_path_matches_when_no_static_match(self, monkeypatch):
        """Parameterized paths match when no static path matches first."""
        from registry.middleware import rbac as rbac_module

        mock_settings = MagicMock()
        mock_settings.API_VERSION = "v1"
        mock_settings.scopes_config = {
            "system-ops": [{"endpoint": "/servers/stats", "method": "GET"}],
            "servers-read": [{"endpoint": "/servers/{server_id}", "method": "GET"}],
        }
        monkeypatch.setattr(rbac_module, "settings", mock_settings)

        middleware = ScopePermissionMiddleware(app=MagicMock())
        # /servers/abc123 should match servers-read {server_id} rule
        result = middleware._has_permission(["servers-read"], "/servers/abc123", "GET")
        assert result is True

    def test_no_rules_match_denies_access(self, monkeypatch):
        """Denies access when no rules match the request."""
        from registry.middleware import rbac as rbac_module

        mock_settings = MagicMock()
        mock_settings.API_VERSION = "v1"
        mock_settings.scopes_config = {
            "servers-read": [{"endpoint": "/servers", "method": "GET"}],
        }
        monkeypatch.setattr(rbac_module, "settings", mock_settings)

        middleware = ScopePermissionMiddleware(app=MagicMock())
        # /agents path doesn't match any rules
        result = middleware._has_permission(["servers-read"], "/agents", "GET")
        assert result is False

    def test_method_mismatch_denies_access(self, monkeypatch):
        """Denies access when method doesn't match."""
        from registry.middleware import rbac as rbac_module

        mock_settings = MagicMock()
        mock_settings.API_VERSION = "v1"
        mock_settings.scopes_config = {
            "servers-read": [{"endpoint": "/servers", "method": "GET"}],
        }
        monkeypatch.setattr(rbac_module, "settings", mock_settings)

        middleware = ScopePermissionMiddleware(app=MagicMock())
        # POST doesn't match GET rule
        result = middleware._has_permission(["servers-read"], "/servers", "POST")
        assert result is False

    def test_wildcard_method_matches_any(self, monkeypatch):
        """Wildcard method matches any HTTP verb."""
        from registry.middleware import rbac as rbac_module

        mock_settings = MagicMock()
        mock_settings.API_VERSION = "v1"
        mock_settings.scopes_config = {
            "servers-write": [{"endpoint": "/servers", "method": "*"}],
        }
        monkeypatch.setattr(rbac_module, "settings", mock_settings)

        middleware = ScopePermissionMiddleware(app=MagicMock())
        assert middleware._has_permission(["servers-write"], "/servers", "GET") is True
        assert middleware._has_permission(["servers-write"], "/servers", "POST") is True
        assert middleware._has_permission(["servers-write"], "/servers", "DELETE") is True


@pytest.mark.unit
class TestIntegrationScenarios:
    """Test RBAC middleware with FastAPI integration."""

    def _build_app(self):
        """Build test FastAPI app."""
        app = FastAPI()

        @app.get("/servers/stats")
        def stats():
            return {"stats": "data"}

        @app.get("/servers/{server_id}")
        def get_server(server_id: str):
            return {"id": server_id}

        @app.get("/servers")
        def list_servers():
            return {"servers": []}

        @app.get("/agents")
        def list_agents():
            return {"agents": []}

        @app.post("/agents")
        def create_agent():
            return {"ok": True}

        @app.get("/auth/me")
        def auth_me():
            return {"user": "test"}

        return app

    def _auth_middleware_factory(self, user_context: dict[str, Any]):
        """Mock auth middleware that sets user context."""

        class _AuthMiddleware:
            def __init__(self, app):
                self.app = app

            async def __call__(self, scope, receive, send):
                if scope["type"] == "http":
                    scope.setdefault("state", {})
                    scope["state"]["user"] = user_context
                    scope["state"]["is_authenticated"] = True
                await self.app(scope, receive, send)

        return _AuthMiddleware

    def test_static_path_matches_before_parameterized(self, monkeypatch):
        """/servers/stats should match system-ops not servers-read."""
        from registry.middleware import rbac as rbac_module

        mock_settings = MagicMock()
        mock_settings.API_VERSION = "v1"
        mock_settings.scopes_config = {
            "system-ops": [{"endpoint": "/servers/stats", "method": "GET"}],
            "servers-read": [{"endpoint": "/servers/{server_id}", "method": "GET"}],
        }
        monkeypatch.setattr(rbac_module, "settings", mock_settings)

        app = self._build_app()
        app.add_middleware(ScopePermissionMiddleware)
        app.add_middleware(self._auth_middleware_factory({"scopes": ["system-ops"]}))

        client = TestClient(app)
        # User with system-ops can access /servers/stats
        resp = client.get("/servers/stats")
        assert resp.status_code == 200

    def test_user_without_system_ops_cannot_access_stats(self, monkeypatch):
        """User with only servers-read cannot access /servers/stats."""
        from registry.middleware import rbac as rbac_module

        mock_settings = MagicMock()
        mock_settings.API_VERSION = "v1"
        mock_settings.scopes_config = {
            "system-ops": [{"endpoint": "/servers/stats", "method": "GET"}],
            "servers-read": [{"endpoint": "/servers/{server_id}", "method": "GET"}],
        }
        monkeypatch.setattr(rbac_module, "settings", mock_settings)

        app = self._build_app()
        app.add_middleware(ScopePermissionMiddleware)
        app.add_middleware(self._auth_middleware_factory({"scopes": ["servers-read"]}))

        client = TestClient(app)
        # User with servers-read cannot access /servers/stats
        resp = client.get("/servers/stats")
        assert resp.status_code == 403

    def test_parameterized_path_works_with_servers_read(self, monkeypatch):
        """User with servers-read can access /servers/{server_id}."""
        from registry.middleware import rbac as rbac_module

        mock_settings = MagicMock()
        mock_settings.API_VERSION = "v1"
        mock_settings.scopes_config = {
            "system-ops": [{"endpoint": "/servers/stats", "method": "GET"}],
            "servers-read": [{"endpoint": "/servers/{server_id}", "method": "GET"}],
        }
        monkeypatch.setattr(rbac_module, "settings", mock_settings)

        app = self._build_app()
        app.add_middleware(ScopePermissionMiddleware)
        app.add_middleware(self._auth_middleware_factory({"scopes": ["servers-read"]}))

        client = TestClient(app)
        # User with servers-read can access /servers/abc123
        resp = client.get("/servers/abc123")
        assert resp.status_code == 200

    def test_unauthenticated_request_passes_through(self, monkeypatch):
        """Unauthenticated requests (public routes) pass through."""
        from registry.middleware import rbac as rbac_module

        mock_settings = MagicMock()
        mock_settings.API_VERSION = "v1"
        mock_settings.scopes_config = {
            "servers-read": [{"endpoint": "/servers", "method": "GET"}],
        }
        monkeypatch.setattr(rbac_module, "settings", mock_settings)

        # Don't add auth middleware - request won't be authenticated
        app = self._build_app()
        app.add_middleware(ScopePermissionMiddleware)

        client = TestClient(app)
        # Without auth middleware, requests pass through (auth middleware handles public paths)
        resp = client.get("/servers")
        assert resp.status_code == 200

    def test_user_without_scopes_gets_403(self, monkeypatch):
        """Authenticated user with no scopes gets 403."""
        from registry.middleware import rbac as rbac_module

        mock_settings = MagicMock()
        mock_settings.API_VERSION = "v1"
        mock_settings.scopes_config = {
            "servers-read": [{"endpoint": "/servers", "method": "GET"}],
        }
        monkeypatch.setattr(rbac_module, "settings", mock_settings)

        app = self._build_app()
        app.add_middleware(ScopePermissionMiddleware)
        app.add_middleware(self._auth_middleware_factory({"scopes": []}))

        client = TestClient(app)
        # User with no scopes gets 403
        resp = client.get("/servers")
        assert resp.status_code == 403

    def test_multiple_scopes_any_match_grants_access(self, monkeypatch):
        """User with multiple scopes - any matching scope grants access."""
        from registry.middleware import rbac as rbac_module

        mock_settings = MagicMock()
        mock_settings.API_VERSION = "v1"
        mock_settings.scopes_config = {
            "servers-read": [{"endpoint": "/servers", "method": "GET"}],
            "agents-read": [{"endpoint": "/agents", "method": "GET"}],
        }
        monkeypatch.setattr(rbac_module, "settings", mock_settings)

        app = self._build_app()
        app.add_middleware(ScopePermissionMiddleware)
        app.add_middleware(self._auth_middleware_factory({"scopes": ["servers-read", "agents-read"]}))

        client = TestClient(app)
        # User can access both endpoints
        assert client.get("/servers").status_code == 200
        assert client.get("/agents").status_code == 200

    def test_wildcard_endpoint_matches_all_paths(self, monkeypatch):
        """Endpoint wildcard '*' matches any path."""
        from registry.middleware import rbac as rbac_module

        mock_settings = MagicMock()
        mock_settings.API_VERSION = "v1"
        mock_settings.scopes_config = {
            "servers-read": [{"endpoint": "*", "method": "GET"}],
        }
        monkeypatch.setattr(rbac_module, "settings", mock_settings)

        app = self._build_app()
        app.add_middleware(ScopePermissionMiddleware)
        app.add_middleware(self._auth_middleware_factory({"scopes": ["servers-read"]}))

        client = TestClient(app)
        # Endpoint wildcard should match any path
        assert client.get("/servers").status_code == 200
        assert client.get("/servers/abc123").status_code == 200
        assert client.get("/auth/me").status_code == 200

    def test_path_wildcard_matches_nested_paths(self, monkeypatch):
        """Path parameter {path} matches nested paths with slashes."""
        from registry.middleware import rbac as rbac_module

        mock_settings = MagicMock()
        mock_settings.API_VERSION = "v1"
        mock_settings.scopes_config = {
            "agents-read": [{"endpoint": "/agents/{path}", "method": "GET"}],
        }
        monkeypatch.setattr(rbac_module, "settings", mock_settings)

        app = self._build_app()

        # Add route that accepts nested paths
        @app.get("/agents/{path:path}")
        def get_agent_nested(path: str):
            return {"path": path}

        app.add_middleware(ScopePermissionMiddleware)
        app.add_middleware(self._auth_middleware_factory({"scopes": ["agents-read"]}))

        client = TestClient(app)
        # {path} should match nested paths with slashes
        resp = client.get("/agents/foo/bar/baz")
        assert resp.status_code == 200
        assert resp.json()["path"] == "foo/bar/baz"

    def test_share_server_permissions(self, monkeypatch):
        """servers-share scope allows sharing MCP server ACLs."""
        from registry.middleware import rbac as rbac_module

        mock_settings = MagicMock()
        mock_settings.API_VERSION = "v1"
        mock_settings.scopes_config = {
            "servers-share": [{"endpoint": "/permissions/mcpServer/{resource_id}", "method": "PUT"}],
        }
        monkeypatch.setattr(rbac_module, "settings", mock_settings)

        app = self._build_app()

        # Add permissions endpoint
        @app.put("/permissions/mcpServer/{resource_id}")
        def share_server(resource_id: str):
            return {"shared": resource_id}

        app.add_middleware(ScopePermissionMiddleware)
        app.add_middleware(self._auth_middleware_factory({"scopes": ["servers-share"]}))

        client = TestClient(app)
        # servers-share should allow PUT on mcpServer ACL endpoint
        resp = client.put("/permissions/mcpServer/abc123")
        assert resp.status_code == 200

    def test_share_agent_permissions(self, monkeypatch):
        """agents-share scope allows sharing agent ACLs."""
        from registry.middleware import rbac as rbac_module

        mock_settings = MagicMock()
        mock_settings.API_VERSION = "v1"
        mock_settings.scopes_config = {
            "agents-share": [{"endpoint": "/permissions/agent/{resource_id}", "method": "PUT"}],
        }
        monkeypatch.setattr(rbac_module, "settings", mock_settings)

        app = self._build_app()

        # Add permissions endpoint
        @app.put("/permissions/agent/{resource_id}")
        def share_agent(resource_id: str):
            return {"shared": resource_id}

        app.add_middleware(ScopePermissionMiddleware)
        app.add_middleware(self._auth_middleware_factory({"scopes": ["agents-share"]}))

        client = TestClient(app)
        # agents-share should allow PUT on agent ACL endpoint
        resp = client.put("/permissions/agent/xyz789")
        assert resp.status_code == 200

    def test_group_to_scope_mapping(self, monkeypatch):
        """Maps groups to scopes when explicit scopes missing."""
        from registry.middleware import rbac as rbac_module

        mock_settings = MagicMock()
        mock_settings.API_VERSION = "v1"
        mock_settings.scopes_config = {
            "group_mappings": {"jarvis-registry-user": ["servers-read"]},
            "servers-read": [{"endpoint": "/servers", "method": "GET"}],
        }
        monkeypatch.setattr(rbac_module, "settings", mock_settings)

        # Mock the group mapping function
        from registry.auth import dependencies as deps_module

        def mock_map_groups(groups):
            mappings = mock_settings.scopes_config.get("group_mappings", {})
            scopes = []
            for group in groups:
                scopes.extend(mappings.get(group, []))
            return scopes

        monkeypatch.setattr(deps_module, "map_cognito_groups_to_scopes", mock_map_groups)

        app = self._build_app()
        app.add_middleware(ScopePermissionMiddleware)
        # User has no explicit scopes, only groups
        app.add_middleware(self._auth_middleware_factory({"scopes": [], "groups": ["jarvis-registry-user"]}))

        client = TestClient(app)
        # Group should be mapped to servers-read scope
        resp = client.get("/servers")
        assert resp.status_code == 200
