import logging
import re
from collections.abc import Iterable
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import compile_path

from registry.auth.dependencies import map_cognito_groups_to_scopes
from registry.core.config import settings

logger = logging.getLogger(__name__)


def _normalize_path(path: str) -> str:
    """
    Normalize request paths to resource paths only.
    Strips /api/v{version} and /api prefixes to get the resource path.
    Examples:
      - /api/v1/servers -> /servers
      - /api/servers -> /servers
      - /servers -> /servers
      - /api/v1/auth/me -> /auth/me
    """
    # Strip /api/v{version} prefix first (more specific)
    api_version_prefix = f"/api/{settings.API_VERSION}"
    if path.startswith(api_version_prefix):
        return path[len(api_version_prefix) :]

    # Strip /api prefix
    if path.startswith("/api"):
        return path[len("/api") :]

    return path


def _normalize_endpoint_pattern(endpoint: str) -> str:
    """
    Normalize scopes.yml endpoint patterns to match FastAPI paths.
    - Replace `{path}` with `{path:path}` to allow slashes.
    """
    endpoint = endpoint.strip()
    endpoint = re.sub(r"\{path\}", "{path:path}", endpoint)
    return endpoint


def _parse_methods(method_value: str) -> set[str] | None:
    """
    Parse method field from scopes.yml.
    Returns:
      - None for wildcard "*"
      - set of methods for comma-separated lists
      - empty set for empty/invalid input
    """
    if not method_value:
        return set()
    method_value = method_value.strip()
    if method_value == "*":
        return None
    parts = [m.strip().upper() for m in method_value.split(",") if m.strip()]
    return set(parts)


class ScopePermissionMiddleware(BaseHTTPMiddleware):
    """
    Enforce endpoint/method permissions based on scopes.yml.

    If an endpoint pattern matches the request path, user must have at least
    one scope whose rules intersect (method + endpoint).

    Notes:
      - Rules are loaded at initialization for optimal performance.
      - Path matching is done against normalized candidates so both /api/*
        and /api/{version} prefixes work.
      - Auth middleware filters public paths, so all authenticated requests
        reaching this middleware should be checked for permissions.
    """

    def __init__(self, app):
        super().__init__(app)
        self._rules: list[dict[str, Any]] = []
        # Load rules at initialization instead of lazily
        self._load_rules()
        logger.info("ScopePermissionMiddleware initialized with %d rules", len(self._rules))

    def _load_rules(self) -> None:
        """
        Load and compile rules from scopes.yml into in-memory structures.
        Called during __init__ for optimal performance.
        """
        config = settings.scopes_config or {}
        rules = []

        # Load all scopes except group_mappings
        for scope_name, scope_rules in config.items():
            if scope_name == "group_mappings":
                continue

            for rule in scope_rules:
                endpoint = rule.get("endpoint", "").strip()
                method = rule.get("method", "").strip().upper()

                if not endpoint:
                    continue

                if endpoint == "*":
                    compiled = None
                else:
                    pattern = _normalize_endpoint_pattern(endpoint)
                    compiled = compile_path(pattern)[0]

                methods = _parse_methods(method)
                rules.append(
                    {
                        "scope": scope_name,
                        "methods": methods,
                        "endpoint": endpoint,
                        "compiled": compiled,
                    }
                )

        # Sort by specificity (static paths before parameterized paths)
        rules.sort(key=self._rule_specificity)

        self._rules = rules
        logger.debug("Loaded %d permission rules from scopes.yml (sorted by specificity)", len(rules))

    @staticmethod
    def _rule_specificity(rule):
        """
        Sort rules by specificity to ensure exact paths are checked before parameterized paths.
        Example: /servers/stats should be checked before /servers/{server_id}
        """
        endpoint = rule["endpoint"]
        param_count = endpoint.count("{")
        segment_count = len(endpoint.split("/"))
        return (param_count, -segment_count, endpoint)

    def _effective_scopes(self, user_context: dict[str, Any]) -> list[str]:
        """
        Determine scopes for the request.
        Prefer explicit scopes on the JWT; otherwise map groups to scopes.
        """
        scopes = user_context.get("scopes") or []
        if scopes:
            return scopes

        groups = user_context.get("groups") or []
        if groups:
            return map_cognito_groups_to_scopes(groups)

        return []

    def _rule_matches(self, rule: dict[str, Any], path: str, method: str) -> bool:
        """
        Check if a rule matches by both method and endpoint.
        """
        # method match
        methods = rule["methods"]
        if methods is not None and method not in methods:
            return False

        # endpoint match
        return self._endpoint_matches(rule, path)

    def _endpoint_matches(self, rule: dict[str, Any], path: str) -> bool:
        """
        Check if the path matches the rule's endpoint pattern.
        """
        if rule["compiled"] is None:
            return True
        return rule["compiled"].match(path) is not None

    def _has_permission(self, user_scopes: Iterable[str], path: str, method: str) -> bool:
        """
        Determine if user has permission to access the given path+method.

        Logic:
        1. Find the first rule that matches (rules are sorted by specificity)
        2. Check if user has the required scope for that rule
        """
        user_scope_set = set(user_scopes)

        # Find the first matching rule (most specific due to sorting)
        for rule in self._rules:
            if self._rule_matches(rule, path, method):
                required_scope = rule["scope"]
                has_scope = required_scope in user_scope_set

                if has_scope:
                    logger.debug(f"Permission granted: user has '{required_scope}' for {method} {path}")
                else:
                    logger.debug(f"Permission denied: user lacks '{required_scope}' for {method} {path}")

                return has_scope

        # No rules match - deny (authenticated paths must have explicit permissions)
        logger.debug(f"No rules match path={path}, method={method} - denying")
        return False

    async def dispatch(self, request: Request, call_next):
        """
        Enforce scope permissions for authenticated requests.

        Since auth middleware already filters public paths, any authenticated
        request reaching this middleware should be checked for permissions.
        """
        # If auth middleware didn't set user, allow through (public routes).
        if not getattr(request.state, "is_authenticated", False):
            return await call_next(request)

        method = request.method.upper()
        normalized_path = _normalize_path(request.url.path)
        logger.debug(f"RBAC check - path: {normalized_path}, method: {method}")

        user_context = getattr(request.state, "user", {}) or {}
        user_scopes = self._effective_scopes(user_context)

        if not user_scopes:
            return JSONResponse(status_code=403, content={"detail": "Insufficient permissions"})

        if self._has_permission(user_scopes, normalized_path, method):
            return await call_next(request)

        return JSONResponse(status_code=403, content={"detail": "Insufficient permissions"})
