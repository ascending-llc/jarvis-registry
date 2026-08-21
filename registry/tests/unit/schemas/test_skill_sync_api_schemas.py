import pytest
from pydantic import ValidationError

from registry.schemas.skill_sync_api_schemas import (
    SkillSyncSourceCreateRequest,
    SkillSyncSourceUpdateRequest,
)


def _valid_create_payload() -> dict:
    return {
        "displayName": "Company skills",
        "owner": "ascending-ai",
        "repo": "skills.repo",
        "paths": ["skills/core"],
        "githubAppClientId": "client-id",
        "githubAppClientSecret": "secret",
    }


def test_create_request_normalizes_paths() -> None:
    request = SkillSyncSourceCreateRequest(**_valid_create_payload())
    assert request.paths == ["skills/core"]
    assert request.ref == "main"


@pytest.mark.parametrize("path", ["../skills", "/skills", "skills\\core", ""])
def test_create_request_rejects_unsafe_paths(path: str) -> None:
    payload = _valid_create_payload()
    payload["paths"] = [path]
    with pytest.raises(ValidationError):
        SkillSyncSourceCreateRequest(**payload)


@pytest.mark.parametrize("ref", ["../main", "/main", "feature//bad", "feature@{bad"])
def test_create_request_rejects_unsafe_refs(ref: str) -> None:
    payload = _valid_create_payload()
    payload["ref"] = ref
    with pytest.raises(ValidationError):
        SkillSyncSourceCreateRequest(**payload)


def test_update_secret_is_optional() -> None:
    request = SkillSyncSourceUpdateRequest(displayName="Renamed")
    assert request.githubAppClientSecret is None
    assert request.model_dump(exclude_unset=True) == {"displayName": "Renamed"}
