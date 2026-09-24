from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beanie import PydanticObjectId

from registry.services.skill_sync_apply_service import SkillSyncApplyService
from registry.services.skill_sync_discovery_service import DiscoveredSkill, DiscoveryResult
from registry.services.skill_sync_github_service import ExtractedAuxFile
from registry.utils.skill_files import is_text_content as _is_text_content
from registry_pkgs.models.enums import SkillSyncSkillErrorCode
from registry_pkgs.models.skill_sync_job import (
    SkillSyncDiscoverySummary,
    SkillSyncFullRequestSnapshot,
    SkillSyncSkillError,
)


class _FakeSession:
    """Runs a with_transaction callback `attempts` times, as pymongo does after transient aborts."""

    def __init__(self, attempts: int = 1):
        self.attempts = attempts
        self.with_transaction_calls = 0

    async def with_transaction(self, callback):
        self.with_transaction_calls += 1
        result = None
        for _ in range(self.attempts):
            result = await callback(self)
        return result


class _FakeSessionContext:
    def __init__(self, session: _FakeSession | None = None):
        self.session = session or _FakeSession()

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *args):
        return None


def _user_service(user=None) -> MagicMock:
    return MagicMock(get_user_by_user_id=AsyncMock(return_value=user))


def _service(acl_service=None, user_service=None) -> SkillSyncApplyService:
    return SkillSyncApplyService(
        acl_service=acl_service or MagicMock(),
        user_service=user_service or _user_service(SimpleNamespace(name="Jane Doe", username="jane")),
    )


def _snapshot() -> SkillSyncFullRequestSnapshot:
    return SkillSyncFullRequestSnapshot(owner="octocat", repo="skills", ref="main", paths=["skills"])


def _discovered(*, files=None) -> DiscoveredSkill:
    return DiscoveredSkill(
        upstream_id="skills/demo",
        name="demo",
        description="Demo skill",
        body="Instructions",
        frontmatter={"license": "MIT"},
        user_invocable=True,
        disable_model_invocation=False,
        allowed_tools=["read"],
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
@pytest.mark.parametrize(
    "error_code",
    [SkillSyncSkillErrorCode.SKILL_PARSE_FAILED, SkillSyncSkillErrorCode.SKILL_NAME_MISMATCH],
)
async def test_discovery_error_preserves_matching_existing_skill(error_code):
    source = SimpleNamespace(id=PydanticObjectId())
    existing = SimpleNamespace(
        id=PydanticObjectId(),
        sourceMetadata={"upstreamId": f"{source.id}:skills/broken"},
    )
    error = SkillSyncSkillError(
        skillPath="skills/broken",
        upstreamId="skills/broken",
        errorCode=error_code,
        errorMessage="discovery failed",
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


def _capture_inserts(skill_file_model) -> list[SimpleNamespace]:
    inserted_files: list[SimpleNamespace] = []

    def _new_file(**kwargs):
        value = SimpleNamespace(**kwargs, insert=AsyncMock())
        inserted_files.append(value)
        return value

    skill_file_model.side_effect = _new_file
    return inserted_files


@pytest.mark.asyncio
async def test_sync_skill_files_updates_text_creates_binary_and_deletes_stale(tmp_path):
    text_path = tmp_path / "README.md"
    text_path.write_text("new text")
    binary_path = tmp_path / "image.bin"
    binary_path.write_bytes(b"\x00\x01")
    existing_text = SimpleNamespace(
        relativePath="README.md",
        source="registry-inline",
        content=None,
        body=b"old",
        mimeType="text/markdown",
        bytes=3,
        isBinary=False,
        updatedAt=None,
        save=AsyncMock(),
        delete=AsyncMock(),
    )
    stale = SimpleNamespace(relativePath="stale.txt", source="registry-inline", delete=AsyncMock())
    finder = MagicMock(to_list=AsyncMock(return_value=[existing_text, stale]))

    discovered = _discovered(
        files=[
            ExtractedAuxFile("README.md", text_path, text_path.stat().st_size, is_executable=True),
            ExtractedAuxFile("image.bin", binary_path, binary_path.stat().st_size, is_executable=False),
        ]
    )
    with patch("registry.services.skill_sync_apply_service.SkillFile") as skill_file:
        skill_file.find.return_value = finder
        inserted_files = _capture_inserts(skill_file)
        counts = await SkillSyncApplyService._sync_skill_files(
            PydanticObjectId(),
            discovered,
            datetime.now(UTC),
            session=MagicMock(),
        )

    assert counts == (1, 1, 1)
    # Text is stored the same way as a file created in Registry: raw bytes in `body`, no `content`.
    assert existing_text.body == b"new text"
    assert existing_text.content is None
    assert existing_text.source == "registry-inline"
    assert existing_text.isExecutable is True
    existing_text.save.assert_awaited_once()
    existing_text.delete.assert_not_awaited()
    assert len(inserted_files) == 1
    assert inserted_files[0].relativePath == "image.bin"
    assert inserted_files[0].source == "registry-inline"
    assert inserted_files[0].isExecutable is False
    assert inserted_files[0].isBinary is True
    assert inserted_files[0].body == b"\x00\x01"
    assert inserted_files[0].content is None
    stale.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_sync_skill_files_stores_new_text_file_inline_in_body(tmp_path):
    script_path = tmp_path / "run.sh"
    script_path.write_text("#!/usr/bin/env bash\necho hi\n")
    finder = MagicMock(to_list=AsyncMock(return_value=[]))
    discovered = _discovered(
        files=[ExtractedAuxFile("scripts/run.sh", script_path, script_path.stat().st_size, is_executable=True)]
    )

    with patch("registry.services.skill_sync_apply_service.SkillFile") as skill_file:
        skill_file.find.return_value = finder
        inserted_files = _capture_inserts(skill_file)
        counts = await SkillSyncApplyService._sync_skill_files(
            PydanticObjectId(), discovered, datetime.now(UTC), session=MagicMock()
        )

    assert counts == (0, 1, 0)
    inserted = inserted_files[0]
    assert inserted.relativePath == "scripts/run.sh"
    assert inserted.source == "registry-inline"
    assert inserted.body == b"#!/usr/bin/env bash\necho hi\n"
    assert inserted.content is None
    assert inserted.isBinary is False
    assert inserted.isExecutable is True
    inserted.insert.assert_awaited_once()


@pytest.mark.asyncio
async def test_sync_skill_files_replaces_a_legacy_github_sync_file_at_the_same_path(tmp_path):
    script_path = tmp_path / "run.sh"
    script_path.write_text("echo new")
    legacy = SimpleNamespace(
        relativePath="scripts/run.sh",
        source="github-sync",
        content="echo old",
        body=None,
        save=AsyncMock(),
        delete=AsyncMock(),
    )
    finder = MagicMock(to_list=AsyncMock(return_value=[legacy]))
    discovered = _discovered(
        files=[ExtractedAuxFile("scripts/run.sh", script_path, script_path.stat().st_size, is_executable=False)]
    )

    with patch("registry.services.skill_sync_apply_service.SkillFile") as skill_file:
        skill_file.find.return_value = finder
        inserted_files = _capture_inserts(skill_file)
        counts = await SkillSyncApplyService._sync_skill_files(
            PydanticObjectId(), discovered, datetime.now(UTC), session=MagicMock()
        )

    assert counts == (1, 0, 0)
    legacy.delete.assert_awaited_once()
    legacy.save.assert_not_awaited()
    assert len(inserted_files) == 1
    assert inserted_files[0].source == "registry-inline"
    assert inserted_files[0].body == b"echo new"
    assert inserted_files[0].content is None


@pytest.mark.asyncio
async def test_sync_skill_files_removes_legacy_files_stored_under_repository_paths(tmp_path):
    """Files synced before the relativePath fix used repository paths; the next sync replaces them."""
    script_path = tmp_path / "run.sh"
    script_path.write_text("echo hi")
    legacy = SimpleNamespace(relativePath="skills/demo/scripts/run.sh", source="github-sync", delete=AsyncMock())
    finder = MagicMock(to_list=AsyncMock(return_value=[legacy]))
    discovered = _discovered(
        files=[ExtractedAuxFile("scripts/run.sh", script_path, script_path.stat().st_size, is_executable=False)]
    )

    with patch("registry.services.skill_sync_apply_service.SkillFile") as skill_file:
        skill_file.find.return_value = finder
        inserted_files = _capture_inserts(skill_file)
        counts = await SkillSyncApplyService._sync_skill_files(
            PydanticObjectId(), discovered, datetime.now(UTC), session=MagicMock()
        )

    assert counts == (0, 1, 1)
    legacy.delete.assert_awaited_once()
    assert inserted_files[0].relativePath == "scripts/run.sh"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user", "expected"),
    [
        (SimpleNamespace(name="Jane Doe", username="jane"), "Jane Doe"),
        (SimpleNamespace(name=None, username="jane"), "jane"),
        (SimpleNamespace(name="  ", username="jane"), "jane"),
        (SimpleNamespace(name=None, username=""), "GitHub Sync"),
        (None, "GitHub Sync"),
    ],
)
async def test_resolve_author_name_prefers_name_then_username_then_fallback(user, expected):
    user_id = str(PydanticObjectId())
    user_service = _user_service(user)

    assert await _service(user_service=user_service)._resolve_author_name(user_id) == expected
    user_service.get_user_by_user_id.assert_awaited_once_with(user_id)


@pytest.mark.asyncio
async def test_apply_passes_the_syncing_users_name_to_new_skills():
    source = SimpleNamespace(id=PydanticObjectId())
    service = _service(user_service=_user_service(SimpleNamespace(name="Jane Doe", username="jane")))
    service.list_live_skills = AsyncMock(return_value=[])
    service._apply_discovered_skill = AsyncMock(return_value=(True, (0, 0, 0)))

    await service.apply_discovered_skills(
        source=source,
        job=SimpleNamespace(skillErrors=[]),
        discovery=DiscoveryResult(skills=[_discovered()], errors=[], summary=SkillSyncDiscoverySummary()),
        user_id=str(PydanticObjectId()),
        commit_sha="a" * 40,
        request_snapshot=_snapshot(),
    )

    assert service._apply_discovered_skill.await_args.kwargs["author_name"] == "Jane Doe"


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
            "Jane Doe",
            datetime.now(UTC),
            session=MagicMock(),
        )

    metadata = skill_model.call_args.kwargs["sourceMetadata"]
    assert skill_model.call_args.kwargs["author"] == author_id
    assert skill_model.call_args.kwargs["authorName"] == "Jane Doe"
    assert result is created
    assert metadata["upstreamId"] == f"{source.id}:skills/demo"
    assert (metadata["owner"], metadata["repo"], metadata["ref"]) == ("octocat", "skills", "main")
    assert skill_model.call_args.kwargs["frontmatter"] == {"license": "MIT"}
    assert skill_model.call_args.kwargs["allowedTools"] == ["read"]
    for bookkeeping_field in ("displayTitle", "category", "alwaysApply", "tags"):
        assert bookkeeping_field not in skill_model.call_args.kwargs
    acl_service.grant_permission.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_skill_replaces_synced_fields_and_preserves_registry_bookkeeping():
    existing = SimpleNamespace(
        displayTitle="Manual title",
        description="old",
        body="old",
        frontmatter={},
        category="old",
        alwaysApply=True,
        userInvocable=False,
        disableModelInvocation=True,
        allowedTools=None,
        tags=["manual"],
        fileCount=0,
        path="old",
        sourceMetadata={"sourceId": "source-1"},
        version=4,
        updatedAt=None,
        save=AsyncMock(),
    )
    now = datetime.now(UTC)
    discovered = _discovered()
    discovered.display_title = "Synced title"
    discovered.category = "synced"
    discovered.always_apply = False
    discovered.tags = ["synced"]

    await SkillSyncApplyService._update_skill(
        existing,
        discovered,
        "a" * 40,
        _snapshot(),
        now,
        version=5,
        session=MagicMock(),
    )

    assert existing.description == "Demo skill"
    assert existing.frontmatter == {"license": "MIT"}
    assert existing.allowedTools == ["read"]
    assert existing.path == "skills/demo"
    assert existing.version == 5
    assert existing.displayTitle == "Manual title"
    assert existing.category == "old"
    assert existing.alwaysApply is True
    assert existing.tags == ["manual"]
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
async def test_delete_source_skills_attempts_every_skill_before_raising() -> None:
    source = SimpleNamespace(id=PydanticObjectId())
    skills = [SimpleNamespace(id=PydanticObjectId()) for _ in range(3)]
    service = _service()
    service.list_live_skills = AsyncMock(return_value=skills)
    service._delete_skill = AsyncMock(side_effect=[1, RuntimeError("delete failed"), 2])

    with pytest.raises(RuntimeError, match="delete failed"):
        await service.delete_source_skills(source)

    assert service._delete_skill.await_count == 3


@pytest.mark.asyncio
async def test_apply_one_skill_shares_transaction_across_skill_files_and_acl(monkeypatch):
    client = MagicMock(start_session=MagicMock(return_value=_FakeSessionContext()))
    monkeypatch.setattr("registry.services.skill_sync_apply_service.MongoDB.get_client", lambda: client)
    service = _service()
    existing = SimpleNamespace(id=PydanticObjectId(), version=1)
    service._update_skill = AsyncMock()
    service._sync_skill_files = AsyncMock(return_value=(1, 2, 3))

    _, counts = await service._apply_discovered_skill(
        existing=existing,
        discovered=_discovered(),
        source=SimpleNamespace(id=PydanticObjectId()),
        commit_sha="a" * 40,
        request_snapshot=_snapshot(),
        author_id=PydanticObjectId(),
        author_name="Jane Doe",
        now=datetime.now(UTC),
    )

    assert counts == (1, 2, 3)
    assert service._update_skill.await_args.kwargs["session"] is service._sync_skill_files.await_args.kwargs["session"]


@pytest.mark.asyncio
async def test_retried_update_bumps_the_version_only_once(monkeypatch):
    session = _FakeSession(attempts=2)
    client = MagicMock(start_session=MagicMock(return_value=_FakeSessionContext(session)))
    monkeypatch.setattr("registry.services.skill_sync_apply_service.MongoDB.get_client", lambda: client)
    service = _service()
    existing = SimpleNamespace(id=PydanticObjectId(), version=4, sourceMetadata={}, save=AsyncMock())
    service._sync_skill_files = AsyncMock(return_value=(1, 0, 0))

    created, counts = await service._apply_discovered_skill(
        existing=existing,
        discovered=_discovered(),
        source=SimpleNamespace(id=PydanticObjectId()),
        commit_sha="a" * 40,
        request_snapshot=_snapshot(),
        author_id=PydanticObjectId(),
        author_name="Jane Doe",
        now=datetime.now(UTC),
    )

    assert session.with_transaction_calls == 1
    assert existing.save.await_count == 2
    assert existing.version == 5
    assert (created, counts) == (False, (1, 0, 0))


@pytest.mark.asyncio
async def test_retried_create_builds_a_new_skill_each_attempt(monkeypatch):
    session = _FakeSession(attempts=2)
    client = MagicMock(start_session=MagicMock(return_value=_FakeSessionContext(session)))
    monkeypatch.setattr("registry.services.skill_sync_apply_service.MongoDB.get_client", lambda: client)
    service = _service()
    built = [SimpleNamespace(id=PydanticObjectId()), SimpleNamespace(id=PydanticObjectId())]
    service._create_skill = AsyncMock(side_effect=built)
    service._sync_skill_files = AsyncMock(return_value=(0, 2, 0))

    created, counts = await service._apply_discovered_skill(
        existing=None,
        discovered=_discovered(),
        source=SimpleNamespace(id=PydanticObjectId()),
        commit_sha="a" * 40,
        request_snapshot=_snapshot(),
        author_id=PydanticObjectId(),
        author_name="Jane Doe",
        now=datetime.now(UTC),
    )

    assert service._create_skill.await_count == 2
    assert [call.args[0] for call in service._sync_skill_files.await_args_list] == [skill.id for skill in built]
    assert (created, counts) == (True, (0, 2, 0))


@pytest.mark.asyncio
async def test_transaction_error_that_outlasts_retries_propagates(monkeypatch):
    from pymongo.errors import OperationFailure

    conflict = OperationFailure("WriteConflict", code=112)
    session = MagicMock(with_transaction=AsyncMock(side_effect=conflict))
    client = MagicMock(start_session=MagicMock(return_value=_FakeSessionContext(session)))
    monkeypatch.setattr("registry.services.skill_sync_apply_service.MongoDB.get_client", lambda: client)

    with pytest.raises(OperationFailure):
        await _service()._apply_discovered_skill(
            existing=None,
            discovered=_discovered(),
            source=SimpleNamespace(id=PydanticObjectId()),
            commit_sha="a" * 40,
            request_snapshot=_snapshot(),
            author_id=PydanticObjectId(),
            author_name="Jane Doe",
            now=datetime.now(UTC),
        )


@pytest.mark.asyncio
async def test_retried_delete_reports_the_last_attempts_count(monkeypatch):
    session = _FakeSession(attempts=2)
    client = MagicMock(start_session=MagicMock(return_value=_FakeSessionContext(session)))
    monkeypatch.setattr("registry.services.skill_sync_apply_service.MongoDB.get_client", lambda: client)
    acl_service = MagicMock(delete_acl_entries_for_resource=AsyncMock())
    skill = SimpleNamespace(id=PydanticObjectId(), delete=AsyncMock())
    finder = MagicMock(
        delete=AsyncMock(side_effect=[SimpleNamespace(deleted_count=3), SimpleNamespace(deleted_count=3)])
    )

    with patch("registry.services.skill_sync_apply_service.SkillFile") as skill_file:
        skill_file.find.return_value = finder
        deleted = await _service(acl_service)._delete_skill(skill)

    assert deleted == 3
    assert skill.delete.await_count == 2
    assert acl_service.delete_acl_entries_for_resource.await_count == 2


def test_text_detection_rejects_nul_and_invalid_utf8():
    assert _is_text_content(b"plain text") is True
    assert _is_text_content(b"text\x00binary") is False
    assert _is_text_content(b"\xff\xfe") is False


def test_text_detection_checks_full_payload_not_just_prefix():
    # 8 KiB of ASCII followed by a NUL and invalid UTF-8 must be classified as binary,
    # otherwise the sync path would lossily decode the tail with errors="replace".
    payload = b"A" * 8192 + b"\x00\xff\xfe"
    assert _is_text_content(payload) is False
    payload_no_nul = b"A" * 8192 + b"\xff\xfe"
    assert _is_text_content(payload_no_nul) is False
