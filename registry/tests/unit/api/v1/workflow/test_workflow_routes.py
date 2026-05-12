from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException, Request

from registry.api.v1.workflow import workflow_routes


def _request_with_headers(headers: dict[str, str]) -> Request:
    request = MagicMock(spec=Request)
    request.headers = headers
    return request


def test_build_registry_token_prefers_authorization_header(monkeypatch: pytest.MonkeyPatch):
    generate_service_jwt = MagicMock(return_value="generated-token")
    monkeypatch.setattr(workflow_routes, "generate_service_jwt", generate_service_jwt)

    token = workflow_routes._build_registry_token(
        _request_with_headers({"Authorization": "Bearer header-token"}),
        {
            "user_id": "user-1",
            "username": "testuser",
            "groups": [],
            "scopes": ["workflow:run"],
            "auth_method": "jwt",
            "provider": "jwt",
            "auth_source": "jwt_auth",
        },
    )

    assert token == "header-token"
    generate_service_jwt.assert_not_called()


def test_build_registry_token_generates_service_jwt_without_authorization_header(monkeypatch: pytest.MonkeyPatch):
    generate_service_jwt = MagicMock(return_value="generated-token")
    monkeypatch.setattr(workflow_routes, "generate_service_jwt", generate_service_jwt)

    token = workflow_routes._build_registry_token(
        _request_with_headers({}),
        {
            "user_id": "user-1",
            "username": "testuser",
            "groups": [],
            "scopes": ["workflow:run"],
            "auth_method": "traditional",
            "provider": "local",
            "auth_source": "jwt_session_auth",
        },
    )

    assert token == "generated-token"
    generate_service_jwt.assert_called_once_with(
        user_id="user-1",
        username="testuser",
        scopes=["workflow:run"],
    )


def test_build_registry_token_requires_user_id_without_authorization_header():
    with pytest.raises(HTTPException) as exc_info:
        workflow_routes._build_registry_token(
            _request_with_headers({}),
            {
                "user_id": None,
                "username": "testuser",
                "groups": [],
                "scopes": ["workflow:run"],
                "auth_method": "traditional",
                "provider": "local",
                "auth_source": "jwt_session_auth",
            },
        )

    assert exc_info.value.status_code == 401
