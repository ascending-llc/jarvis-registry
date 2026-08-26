from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beanie import PydanticObjectId

from registry.services.skill_sync_apply_service import SkillSyncApplyService, _is_text_content
from registry.services.skill_sync_discovery_service import DiscoveredSkill, DiscoveryResult
from registry.services.skill_sync_github_service import ExtractedAuxFile
from registry_pkgs.models.enums import SkillSyncSkillErrorCode
from registry_pkgs.models.skill_sync_job import (
    SkillSyncDiscoverySummary,
    SkillSyncFullRequestSnapshot,
    SkillSyncSkillError,
)


class _FakeTransaction:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *args):
        return None


class _FakeSession:
    async def start_transaction(self):
        return _FakeTransaction()


class _FakeSessionContext:
    async def __aenter__(self):
        return _FakeSession()

    async def __aexit__(self, *args):
        return None


def _service(acl_service=None) -> SkillSyncApplyService:
    return SkillSyncApplyService(acl_service=acl_service or MagicMock())


def _snapshot() -> SkillSyncFullRequestSnapshot:
    return SkillSyncFullRequestSnapshot(owner="octocat", repo="skills", ref="main", paths=["skills"])


def _discovered(*, files=None) -> DiscoveredSkill:
    return DiscoveredSkill(
        upstream_id="skills/demo",
        name="demo",
        description="Demo skill",
        display_title="Demo",
        body="Instructions",
        frontmatter={"name": "demo", "description": "Demo skill"},
        category="general",
        always_apply=False,
        user_invocable=True,
        disable_model_invocation=False,
        allowed_tools=["read"],
        tags=["demo"],
        files=files or [],
    )


def _acl_entry(principal_id, resource_id, perm_bits=7):
    return SimpleNamespace(
        principalType="user",
        principalId=principal_id,
        resourceType="skillSyncSource",
        resourceId=resource_id,
        permBits=perm_bits,
    )


@pytest.mark.asyncio
async def test_build_source_stats_counts_live_skill_files():
    skills = [SimpleNamespace(id=PydanticObjectId()), SimpleNamespace(id=PydanticObjectId())]
    finder = MagicMock(count=AsyncMock(return_value=3))

    with patch("registry.services.skill_sync_apply_service.SkillFile") as skill_file:
        skill_file.find.return_value = finder
        stats = await SkillSyncApplyService.build_source_stats(skills)

    assert stats.skillCount == 2
    assert stats.fileCount == 3


@pytest.mark.asyncio
async def test_inherit_source_acl_inserts_only_missing_principal_skill_pairs():
    source = SimpleNamespace(id=PydanticObjectId())
    first_skill_id = PydanticObjectId()
    second_skill_id = PydanticObjectId()
    principal_id = PydanticObjectId()
    source_entry = _acl_entry(principal_id, source.id)
    source_find = MagicMock(to_list=AsyncMock(return_value=[source_entry]))
    existing_find = MagicMock(
        to_list=AsyncMock(return_value=[SimpleNamespace(resourceId=first_skill_id, principalId=principal_id)])
    )

    with (
        patch("registry.services.skill_sync_apply_service.RegistryAclEntry") as acl_entry,
        patch("registry.services.skill_sync_apply_service.RegistryAccessRole") as access_role,
    ):
        acl_entry.find.side_effect = [source_find, existing_find]
        acl_entry.insert_many = AsyncMock()
        access_role.find.return_value = MagicMock(
            to_list=AsyncMock(return_value=[SimpleNamespace(permBits=7, id=PydanticObjectId())])
        )
        await _service().inherit_source_acl_to_skills(source, [first_skill_id, second_skill_id])

    assert len(acl_entry.insert_many.await_args.args[0]) == 1
    assert acl_entry.call_args.kwargs["resourceId"] == second_skill_id
    assert acl_entry.call_args.kwargs["principalId"] == principal_id


@pytest.mark.asyncio
async def test_discovery_error_preserves_matching_existing_skill():
    source = SimpleNamespace(id=PydanticObjectId())
    existing = SimpleNamespace(
        id=PydanticObjectId(),
        sourceMetadata={"upstreamId": f"{source.id}:skills/broken"},
    )
    error = SkillSyncSkillError(
        skillPath="skills/broken",
        upstreamId="skills/broken",
        errorCode=SkillSyncSkillErrorCode.SKILL_PARSE_FAILED,
        errorMessage="invalid YAML",
        phase="discovery",
    )
    service = _service()
    service.list_live_skills = AsyncMock(return_value=[existing])
    service._delete_skill = AsyncMock()

    summary = await service.apply_discovered_skills(
        source=source,
        job=SimpleNamespace(skillErrors=[]),
        discovery=DiscoveryResult(skills=[], errors=[error], summary=SkillSyncDiscoverySummary()),
        user_id=str(PydanticObjectId()),
        commit_sha="a" * 40,
        request_snapshot=_snapshot(),
    )

    assert summary.skillsDeleted == 0
    service._delete_skill.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_continues_after_one_skill_write_failure_and_records_error():
    source = SimpleNamespace(id=PydanticObjectId())
    job = SimpleNamespace(skillErrors=[])
    service = _service()
    service.list_live_skills = AsyncMock(return_value=[])
    service._apply_discovered_skill = AsyncMock(side_effect=[RuntimeError("write failed"), (True, (0, 2, 0))])
    first = _discovered()
    second = _discovered()
    second.upstream_id = "skills/second"

    summary = await service.apply_discovered_skills(
        source=source,
        job=job,
        discovery=DiscoveryResult(skills=[first, second], errors=[], summary=SkillSyncDiscoverySummary()),
        user_id=str(PydanticObjectId()),
        commit_sha="a" * 40,
        request_snapshot=_snapshot(),
    )

    assert summary.skillsCreated == 1
    assert summary.skillsFailed == 1
    assert summary.filesCreated == 2
    assert job.skillErrors[0].errorCode == SkillSyncSkillErrorCode.WRITE_FAILED


@pytest.mark.asyncio
async def test_apply_deletes_stale_skill_and_counts_existing_update():
    source = SimpleNamespace(id=PydanticObjectId())
    stale = SimpleNamespace(id=PydanticObjectId(), sourceMetadata={"upstreamId": f"{source.id}:skills/stale"})
    existing = SimpleNamespace(id=PydanticObjectId(), sourceMetadata={"upstreamId": f"{source.id}:skills/demo"})
    service = _service()
    service.list_live_skills = AsyncMock(return_value=[stale, existing])
    service._delete_skill = AsyncMock(return_value=2)
    service._apply_discovered_skill = AsyncMock(return_value=(False, (1, 2, 3)))

    summary = await service.apply_discovered_skills(
        source=source,
        job=SimpleNamespace(skillErrors=[]),
        discovery=DiscoveryResult(skills=[_discovered()], errors=[], summary=SkillSyncDiscoverySummary()),
        user_id=str(PydanticObjectId()),
        commit_sha="a" * 40,
        request_snapshot=_snapshot(),
    )

    assert summary.skillsDeleted == 1
    assert summary.skillsUpdated == 1
    assert (summary.filesUpdated, summary.filesCreated, summary.filesDeleted) == (1, 2, 3)
    service._delete_skill.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_records_error_when_stale_skill_delete_fails():
    source = SimpleNamespace(id=PydanticObjectId())
    stale = SimpleNamespace(
        id=PydanticObjectId(),
        sourceMetadata={"upstreamId": f"{source.id}:skills/stale", "skillPath": "skills/stale"},
    )
    job = SimpleNamespace(skillErrors=[])
    service = _service()
    service.list_live_skills = AsyncMock(return_value=[stale])
    service._delete_skill = AsyncMock(side_effect=RuntimeError("delete failed"))

    summary = await service.apply_discovered_skills(
        source=source,
        job=job,
        discovery=DiscoveryResult(skills=[], errors=[], summary=SkillSyncDiscoverySummary()),
        user_id=str(PydanticObjectId()),
        commit_sha="a" * 40,
        request_snapshot=_snapshot(),
    )

    assert summary.skillsFailed == 1
    assert summary.skillsDeleted == 0
    assert job.skillErrors[0].errorCode == SkillSyncSkillErrorCode.DELETE_FAILED
    assert job.skillErrors[0].skillPath == "skills/stale"
    assert job.skillErrors[0].phase == "delete"
    assert "delete failed" in job.skillErrors[0].errorMessage


@pytest.mark.asyncio
async def test_sync_skill_files_updates_text_creates_binary_and_deletes_stale(tmp_path):
    text_path = tmp_path / "README.md"
    text_path.write_text("new text")
    binary_path = tmp_path / "image.bin"
    binary_path.write_bytes(b"\x00\x01")
    existing_text = SimpleNamespace(
        relativePath="README.md",
        content="old",
        body=None,
        mimeType="text/markdown",
        bytes=3,
        isBinary=False,
        updatedAt=None,
        save=AsyncMock(),
        delete=AsyncMock(),
    )
    stale = SimpleNamespace(relativePath="stale.txt", delete=AsyncMock())
    finder = MagicMock(to_list=AsyncMock(return_value=[existing_text, stale]))
    inserted_files = []

    def _new_file(**kwargs):
        value = SimpleNamespace(**kwargs, insert=AsyncMock())
        inserted_files.append(value)
        return value

    discovered = _discovered(
        files=[
            ExtractedAuxFile("README.md", text_path, text_path.stat().st_size),
            ExtractedAuxFile("image.bin", binary_path, binary_path.stat().st_size),
        ]
    )
    with patch("registry.services.skill_sync_apply_service.SkillFile") as skill_file:
        skill_file.find.return_value = finder
        skill_file.side_effect = _new_file
        counts = await SkillSyncApplyService._sync_skill_files(
            PydanticObjectId(),
            discovered,
            datetime.now(UTC),
            session=MagicMock(),
        )

    assert counts == (1, 1, 1)
    assert existing_text.content == "new text"
    assert existing_text.body is None
    assert inserted_files[0].isBinary is True
    assert inserted_files[0].body == b"\x00\x01"
    stale.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_skill_uses_snapshot_metadata_and_grants_owner():
    source = SimpleNamespace(id=PydanticObjectId())
    author_id = PydanticObjectId()
    acl_service = MagicMock(grant_permission=AsyncMock())
    created = SimpleNamespace(id=PydanticObjectId(), insert=AsyncMock())

    with patch("registry.services.skill_sync_apply_service.Skill", return_value=created) as skill_model:
        result = await _service(acl_service)._create_skill(
            _discovered(),
            source,
            "a" * 40,
            _snapshot(),
            author_id,
            datetime.now(UTC),
            session=MagicMock(),
        )

    metadata = skill_model.call_args.kwargs["sourceMetadata"]
    assert result is created
    assert metadata["upstreamId"] == f"{source.id}:skills/demo"
    assert (metadata["owner"], metadata["repo"], metadata["ref"]) == ("octocat", "skills", "main")
    acl_service.grant_permission.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_skill_replaces_synced_fields_and_increments_version():
    existing = SimpleNamespace(
        displayTitle=None,
        description="old",
        body="old",
        frontmatter={},
        category="old",
        alwaysApply=True,
        userInvocable=False,
        disableModelInvocation=True,
        allowedTools=None,
        tags=[],
        fileCount=0,
        path="old",
        sourceMetadata={"sourceId": "source-1"},
        version=4,
        updatedAt=None,
        save=AsyncMock(),
    )
    now = datetime.now(UTC)

    await SkillSyncApplyService._update_skill(
        existing,
        _discovered(),
        "a" * 40,
        _snapshot(),
        now,
        session=MagicMock(),
    )

    assert existing.description == "Demo skill"
    assert existing.path == "skills/demo"
    assert existing.version == 5
    assert existing.sourceMetadata["sourceId"] == "source-1"
    assert existing.sourceMetadata["commitSha"] == "a" * 40
    existing.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_source_skills_returns_real_deleted_counts(monkeypatch):
    client = MagicMock(start_session=MagicMock(return_value=_FakeSessionContext()))
    monkeypatch.setattr("registry.services.skill_sync_apply_service.MongoDB.get_client", lambda: client)
    source = SimpleNamespace(id=PydanticObjectId())
    skills = [
        SimpleNamespace(id=PydanticObjectId(), delete=AsyncMock(), save=AsyncMock()),
        SimpleNamespace(id=PydanticObjectId(), delete=AsyncMock(), save=AsyncMock()),
    ]
    service = _service(MagicMock(delete_acl_entries_for_resource=AsyncMock()))
    service.list_live_skills = AsyncMock(return_value=skills)
    delete_result = SimpleNamespace(deleted_count=2)
    finder = MagicMock(delete=AsyncMock(return_value=delete_result))

    with patch("registry.services.skill_sync_apply_service.SkillFile") as skill_file:
        skill_file.find.return_value = finder
        summary = await service.delete_source_skills(source)

    assert summary.skillsDeleted == 2
    assert summary.filesDeleted == 4
    # Hard delete: the document is removed outright, never re-saved with a tombstone.
    assert all(skill.delete.await_count == 1 for skill in skills)
    assert all(skill.save.await_count == 0 for skill in skills)


@pytest.mark.asyncio
async def test_apply_one_skill_shares_transaction_across_skill_files_and_acl(monkeypatch):
    client = MagicMock(start_session=MagicMock(return_value=_FakeSessionContext()))
    monkeypatch.setattr("registry.services.skill_sync_apply_service.MongoDB.get_client", lambda: client)
    service = _service()
    existing = SimpleNamespace(id=PydanticObjectId())
    service._update_skill = AsyncMock()
    service._sync_skill_files = AsyncMock(return_value=(1, 2, 3))

    _, counts = await service._apply_discovered_skill(
        existing=existing,
        discovered=_discovered(),
        source=SimpleNamespace(id=PydanticObjectId()),
        commit_sha="a" * 40,
        request_snapshot=_snapshot(),
        author_id=PydanticObjectId(),
        now=datetime.now(UTC),
    )

    assert counts == (1, 2, 3)
    assert service._update_skill.await_args.kwargs["session"] is service._sync_skill_files.await_args.kwargs["session"]


def test_text_detection_rejects_nul_and_invalid_utf8():
    assert _is_text_content(b"plain text") is True
    assert _is_text_content(b"text\x00binary") is False
    assert _is_text_content(b"\xff\xfe") is False
