"""Integration tests for Skills Sync proxy endpoints."""

from collections.abc import Generator
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beanie import PydanticObjectId
from fastapi import FastAPI
from fastapi.testclient import TestClient

from registry.api.v1.skill.skill_routes import router as skill_router
from registry.core.config import settings
from registry.middleware.auth import UnifiedAuthMiddleware
from registry.middleware.rbac import ScopePermissionMiddleware
from registry.services.skill_service import SkillContentUnavailableError
from registry_pkgs.core.jwt_tokens import mint_managed_agent_token

pytestmark = [pytest.mark.integration, pytest.mark.proxy]

_USER_ID = "000000000000000000000001"
_ORIGINAL_HAS_PERMISSION = ScopePermissionMiddleware._has_permission


def _mint_token(scope: str = "skills-proxy-ops") -> str:
    return mint_managed_agent_token(
        settings.jwt_token_config,
        subject="integration-user",
        client_id="test-cli-client",
        expires_in_seconds=3600,
        extra_claims={
            "user_id": _USER_ID,
            "scope": scope,
        },
    )


def _make_skill(
    skill_id: PydanticObjectId | None = None,
    name: str = "test-skill",
    body: str = "# Test",
    content_hash: str = "abc123def456",
) -> MagicMock:
    skill = MagicMock()
    skill.id = skill_id or PydanticObjectId()
    skill.name = name
    skill.description = "A test skill"
    skill.body = body
    skill.category = "testing"
    skill.version = 1
    skill.fileCount = 0
    skill.alwaysApply = False
    skill.frontmatter = {}
    skill.tags = ["python", "test"]
    skill.path = None
    skill.deletedAt = None
    skill.contentHash = content_hash
    skill.updatedAt = datetime(2026, 8, 1, tzinfo=UTC)
    return skill


def _make_skill_file(relative_path: str, content: str = "data") -> MagicMock:
    sf = MagicMock()
    sf.relativePath = relative_path
    sf.content = content
    sf.mimeType = "text/plain"
    sf.bytes = len(content)
    sf.isBinary = None
    return sf


@pytest.fixture
def skill_app(monkeypatch) -> Generator[SimpleNamespace, None, None]:
    monkeypatch.setattr(ScopePermissionMiddleware, "_has_permission", _ORIGINAL_HAS_PERMISSION)
    app = FastAPI()
    app.add_middleware(ScopePermissionMiddleware)
    app.add_middleware(UnifiedAuthMiddleware)
    app.include_router(skill_router, prefix="/proxy")

    with TestClient(app) as client:
        yield SimpleNamespace(client=client)


class TestListSkills:
    @patch("registry.api.v1.skill.skill_routes.list_skills_delta", new_callable=AsyncMock)
    def test_list_skills_returns_200(self, mock_list, skill_app):
        skill = _make_skill(content_hash="stored_hash_value")
        mock_list.return_value = [skill]

        resp = skill_app.client.get(
            "/proxy/skills",
            headers={"Authorization": f"Bearer {_mint_token()}"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["skills"]) == 1
        assert data["skills"][0]["name"] == "test-skill"
        assert data["skills"][0]["contentHash"] == "stored_hash_value"
        assert data["cursor"] is not None

        mock_list.assert_awaited_once_with(None)

    @patch("registry.api.v1.skill.skill_routes.list_skills_delta", new_callable=AsyncMock)
    def test_list_skills_empty(self, mock_list, skill_app):
        mock_list.return_value = []

        resp = skill_app.client.get(
            "/proxy/skills",
            headers={"Authorization": f"Bearer {_mint_token()}"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["skills"] == []
        assert data["cursor"] is None

    @patch("registry.api.v1.skill.skill_routes.list_skills_delta", new_callable=AsyncMock)
    def test_list_skills_with_since_param(self, mock_list, skill_app):
        mock_list.return_value = []

        resp = skill_app.client.get(
            "/proxy/skills?since=2026-08-01T00:00:00Z",
            headers={"Authorization": f"Bearer {_mint_token()}"},
        )

        assert resp.status_code == 200

    def test_list_skills_no_auth_returns_401(self, skill_app):
        resp = skill_app.client.get("/proxy/skills")
        assert resp.status_code == 401

    def test_list_skills_requires_skills_proxy_scope(self, skill_app):
        resp = skill_app.client.get(
            "/proxy/skills",
            headers={"Authorization": f"Bearer {_mint_token('mcp-proxy-ops')}"},
        )

        assert resp.status_code == 403

    @patch("registry.api.v1.skill.skill_routes.list_skills_delta", new_callable=AsyncMock)
    def test_list_skills_includes_tags_and_path(self, mock_list, skill_app):
        skill = _make_skill()
        skill.tags = ["ai", "coding"]
        skill.path = "my-skill"
        mock_list.return_value = [skill]

        resp = skill_app.client.get(
            "/proxy/skills",
            headers={"Authorization": f"Bearer {_mint_token()}"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["skills"][0]["tags"] == ["ai", "coding"]
        assert data["skills"][0]["path"] == "my-skill"

    @patch("registry.api.v1.skill.skill_routes.list_skills_delta", new_callable=AsyncMock)
    def test_list_skills_unreconstructable_content_returns_409(self, mock_list, skill_app):
        mock_list.side_effect = SkillContentUnavailableError("content unavailable")

        resp = skill_app.client.get(
            "/proxy/skills",
            headers={"Authorization": f"Bearer {_mint_token()}"},
        )

        assert resp.status_code == 409
        assert resp.json()["detail"] == "content unavailable"


class TestGetSkillContent:
    @patch("registry.api.v1.skill.skill_routes.get_skill_with_files", new_callable=AsyncMock)
    def test_get_content_returns_200(self, mock_get, skill_app):
        skill_id = PydanticObjectId()
        skill = _make_skill(skill_id=skill_id, body="# Hello", content_hash="persisted_hash")
        files = [_make_skill_file("scripts/run.sh", "#!/bin/bash")]
        mock_get.return_value = (skill, files)

        resp = skill_app.client.get(
            f"/proxy/skills/{skill_id}/content",
            headers={"Authorization": f"Bearer {_mint_token()}"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == str(skill_id)
        assert data["body"] == "# Hello"
        assert data["contentHash"] == "persisted_hash"
        assert len(data["files"]) == 1
        assert data["files"][0]["relativePath"] == "scripts/run.sh"
        assert data["files"][0]["isBinary"] is None
        assert data["files"][0]["bytes"] == len("#!/bin/bash")
        assert "size" not in data["files"][0]

    @patch("registry.api.v1.skill.skill_routes.get_skill_with_files", new_callable=AsyncMock)
    def test_get_content_not_found(self, mock_get, skill_app):
        mock_get.side_effect = ValueError("Skill 000000000000000000000000 not found")

        resp = skill_app.client.get(
            f"/proxy/skills/{PydanticObjectId()}/content",
            headers={"Authorization": f"Bearer {_mint_token()}"},
        )

        assert resp.status_code == 404

    def test_get_content_no_auth_returns_401(self, skill_app):
        resp = skill_app.client.get(f"/proxy/skills/{PydanticObjectId()}/content")
        assert resp.status_code == 401

    @patch("registry.api.v1.skill.skill_routes.get_skill_with_files", new_callable=AsyncMock)
    def test_binary_file_marked_correctly(self, mock_get, skill_app):
        skill_id = PydanticObjectId()
        skill = _make_skill(skill_id=skill_id)
        binary_file = MagicMock()
        binary_file.relativePath = "assets/logo.png"
        binary_file.content = None
        binary_file.mimeType = "image/png"
        binary_file.bytes = 4096
        binary_file.isBinary = True
        mock_get.return_value = (skill, [binary_file])

        resp = skill_app.client.get(
            f"/proxy/skills/{skill_id}/content",
            headers={"Authorization": f"Bearer {_mint_token()}"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["files"][0]["isBinary"] is True
        assert data["files"][0]["content"] is None

    @patch("registry.api.v1.skill.skill_routes.get_skill_with_files", new_callable=AsyncMock)
    def test_text_file_marked_correctly(self, mock_get, skill_app):
        skill_id = PydanticObjectId()
        skill = _make_skill(skill_id=skill_id)
        text_file = _make_skill_file("references/guide.md", "# Guide")
        text_file.isBinary = False
        mock_get.return_value = (skill, [text_file])

        resp = skill_app.client.get(
            f"/proxy/skills/{skill_id}/content",
            headers={"Authorization": f"Bearer {_mint_token()}"},
        )

        assert resp.status_code == 200
        assert resp.json()["files"][0]["isBinary"] is False

    @patch("registry.api.v1.skill.skill_routes.get_skill_with_files", new_callable=AsyncMock)
    def test_unknown_binary_state_is_not_inferred_from_missing_content(self, mock_get, skill_app):
        skill_id = PydanticObjectId()
        skill = _make_skill(skill_id=skill_id)
        unclassified_file = _make_skill_file("assets/data.bin", "")
        unclassified_file.content = None
        unclassified_file.bytes = 4096
        unclassified_file.isBinary = None
        mock_get.return_value = (skill, [unclassified_file])

        resp = skill_app.client.get(
            f"/proxy/skills/{skill_id}/content",
            headers={"Authorization": f"Bearer {_mint_token()}"},
        )

        assert resp.status_code == 200
        assert resp.json()["files"][0]["isBinary"] is None

    @patch("registry.api.v1.skill.skill_routes.get_skill_with_files", new_callable=AsyncMock)
    def test_get_content_unreconstructable_content_returns_409(self, mock_get, skill_app):
        mock_get.side_effect = SkillContentUnavailableError("binary content unavailable")

        resp = skill_app.client.get(
            f"/proxy/skills/{PydanticObjectId()}/content",
            headers={"Authorization": f"Bearer {_mint_token()}"},
        )

        assert resp.status_code == 409
        assert resp.json()["detail"] == "binary content unavailable"
