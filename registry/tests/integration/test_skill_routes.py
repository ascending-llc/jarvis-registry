"""Integration tests for Skill CRUD route contracts."""

from collections.abc import Generator
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from registry.api.v1.skill.skill_routes import router as skill_router
from registry.auth.dependencies import get_current_user
from registry.deps import get_skill_service
from registry.schemas.acl_schema import ResourcePermissions
from registry.schemas.skill_api_schemas import SkillFileContentResponse, SkillFileResponse

pytestmark = pytest.mark.integration

_USER_ID = "000000000000000000000001"
_PERMISSIONS = ResourcePermissions(VIEW=True, EDIT=True, DELETE=True, SHARE=True)


def _make_skill(skill_id: PydanticObjectId | None = None) -> MagicMock:
    skill = MagicMock()
    skill.id = skill_id or PydanticObjectId()
    skill.name = "test-skill"
    skill.displayTitle = "Test Skill"
    skill.description = "A test skill"
    skill.body = "# Test"
    skill.frontmatter = {"name": "test-skill"}
    skill.category = "testing"
    skill.tags = ["python"]
    skill.path = "test-skill"
    skill.version = 1
    skill.fileCount = 0
    skill.enabled = True
    skill.alwaysApply = False
    skill.userInvocable = True
    skill.disableModelInvocation = False
    skill.allowedTools = None
    skill.author = PydanticObjectId(_USER_ID)
    skill.authorName = "Integration User"
    skill.source = "inline"
    skill.sourceMetadata = None
    skill.createdAt = datetime(2026, 8, 1, tzinfo=UTC)
    skill.updatedAt = datetime(2026, 8, 2, tzinfo=UTC)
    return skill


def _make_file() -> MagicMock:
    skill_file = MagicMock()
    skill_file.id = PydanticObjectId()
    skill_file.relativePath = "references/guide.md"
    skill_file.mimeType = "text/markdown"
    skill_file.bytes = 7
    skill_file.isBinary = False
    skill_file.isExecutable = False
    skill_file.source = "registry-inline"
    return skill_file


@pytest.fixture
def skill_app() -> Generator[SimpleNamespace, None, None]:
    service = MagicMock()
    service.list_skills = AsyncMock()
    service.create_skill = AsyncMock()
    service.get_skill = AsyncMock()
    service.get_skill_with_files = AsyncMock()
    service.get_skill_file_content = AsyncMock()
    service.update_skill = AsyncMock()
    service.delete_skill = AsyncMock()
    service.toggle_skill = AsyncMock()

    app = FastAPI()
    app.include_router(skill_router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": _USER_ID,
        "client_id": "browser",
        "username": "Integration User",
        "groups": [],
        "scopes": ["skills-read", "skills-write"],
        "auth_method": "oauth2",
        "provider": "entra",
        "auth_source": "jwt_session_auth",
    }
    app.dependency_overrides[get_skill_service] = lambda: service

    with TestClient(app) as client:
        yield SimpleNamespace(client=client, service=service)


def test_list_skills_returns_acl_metadata_and_forwards_filters(skill_app):
    skill = _make_skill()
    skill_app.service.list_skills.return_value = [(skill, _PERMISSIONS)]

    response = skill_app.client.get("/api/v1/skills?enabled=true&fileCount=0")

    assert response.status_code == 200
    item = response.json()["skills"][0]
    assert item["name"] == "test-skill"
    assert item["enabled"] is True
    assert item["permissions"]["EDIT"] is True
    skill_app.service.list_skills.assert_awaited_once_with(user_id=_USER_ID, enabled=True, file_count=0)


def test_create_skill_returns_201(skill_app):
    skill = _make_skill()
    skill_app.service.create_skill.return_value = (skill, _PERMISSIONS)

    response = skill_app.client.post(
        "/api/v1/skills",
        json={"name": "test-skill", "description": "A test skill", "body": "# Test"},
    )

    assert response.status_code == 201
    assert response.json()["source"] == "inline"
    assert response.json()["displayTitle"] == "Test Skill"
    skill_app.service.create_skill.assert_awaited_once()


def test_get_skill_returns_file_metadata_without_content(skill_app):
    skill = _make_skill()
    skill_app.service.get_skill.return_value = (skill, [_make_file()], _PERMISSIONS)

    response = skill_app.client.get(f"/api/v1/skills/{skill.id}")

    assert response.status_code == 200
    assert response.json()["files"][0]["relativePath"] == "references/guide.md"
    assert "content" not in response.json()["files"][0]


def test_get_sync_content_preserves_cli_shape(skill_app):
    skill = _make_skill()
    file_response = SkillFileResponse(
        relativePath="references/guide.md",
        content="# Guide",
        mimeType="text/markdown",
        bytes=7,
        isBinary=False,
        source="registry-inline",
    )
    skill_app.service.get_skill_with_files.return_value = (skill, [file_response])

    response = skill_app.client.get(f"/api/v1/skills/{skill.id}/content")

    assert response.status_code == 200
    assert response.json()["body"] == "# Test"
    assert response.json()["files"][0]["content"] == "# Guide"


def test_get_chat_file_returns_200_with_available_false(skill_app):
    skill = _make_skill()
    skill_app.service.get_skill_file_content.return_value = SkillFileContentResponse(
        relativePath="references/guide.md",
        mimeType="text/markdown",
        available=False,
        unavailableReason="File content is not available in Registry.",
    )

    response = skill_app.client.get(f"/api/v1/skills/{skill.id}/files/references/guide.md")

    assert response.status_code == 200
    assert response.json()["available"] is False


def test_update_skill_returns_incremented_version(skill_app):
    skill = _make_skill()
    skill.version = 2
    skill.description = "Updated"
    skill_app.service.update_skill.return_value = (skill, [], _PERMISSIONS)

    response = skill_app.client.patch(f"/api/v1/skills/{skill.id}", json={"description": "Updated"})

    assert response.status_code == 200
    assert response.json()["version"] == 2


def test_delete_skill_returns_204(skill_app):
    skill_id = PydanticObjectId()

    response = skill_app.client.delete(f"/api/v1/skills/{skill_id}")

    assert response.status_code == 204
    skill_app.service.delete_skill.assert_awaited_once_with(skill_id, _USER_ID)


def test_toggle_skill_returns_new_state(skill_app):
    skill = _make_skill()
    skill.enabled = False
    skill_app.service.toggle_skill.return_value = skill

    response = skill_app.client.post(f"/api/v1/skills/{skill.id}/toggle", json={"enabled": False})

    assert response.status_code == 200
    assert response.json() == {"id": str(skill.id), "enabled": False}


def test_service_http_error_is_preserved(skill_app):
    skill_app.service.get_skill.side_effect = HTTPException(status_code=403, detail="forbidden")

    response = skill_app.client.get(f"/api/v1/skills/{PydanticObjectId()}")

    assert response.status_code == 403
    assert response.json() == {"detail": "forbidden"}


def test_create_duplicate_returns_409(skill_app):
    skill_app.service.create_skill.side_effect = HTTPException(
        status_code=409, detail="A skill with this name already exists"
    )

    response = skill_app.client.post(
        "/api/v1/skills",
        json={"name": "existing-skill", "description": "dup"},
    )

    assert response.status_code == 409


def test_delete_chat_skill_returns_409(skill_app):
    skill_app.service.delete_skill.side_effect = HTTPException(
        status_code=409, detail="Only skills created in Registry can be deleted from Registry"
    )

    response = skill_app.client.delete(f"/api/v1/skills/{PydanticObjectId()}")

    assert response.status_code == 409


def test_list_skills_returns_empty_list(skill_app):
    skill_app.service.list_skills.return_value = []

    response = skill_app.client.get("/api/v1/skills")

    assert response.status_code == 200
    assert response.json() == {"skills": []}


@pytest.mark.parametrize(
    "name",
    [
        "Test Skill",
        "test--skill",
        "test-skill-",
        "-test-skill",
        "UPPER",
        "has space",
    ],
)
def test_create_name_validation_rejects_invalid_pattern(skill_app, name):
    response = skill_app.client.post(
        "/api/v1/skills",
        json={"name": name, "description": "test"},
    )

    assert response.status_code == 422
