"""Unit tests for SkillScanService: status derivation, stale/outdated, 200-vs-202, batch summaries."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beanie import PydanticObjectId
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from registry.schemas.acl_schema import ResourcePermissions
from registry.services.skill_scan_service import SkillScanService, _result_outdated, _scan_status
from registry_pkgs.models.enums import (
    ScanSeverity,
    SkillScanJobStatus,
    SkillScanResultStatus,
    SkillScanTriggerType,
)

_USER_ID = "000000000000000000000001"
_DIGEST = "sha256:abc"


def _acl() -> MagicMock:
    acl = MagicMock()
    acl.check_user_permission = AsyncMock(return_value=ResourcePermissions(VIEW=True, EDIT=True, DELETE=True))
    return acl


def _skill(version: int = 3) -> SimpleNamespace:
    return SimpleNamespace(id=PydanticObjectId(), version=version)


def _summary(*, skill_version: int, tools: list) -> SimpleNamespace:
    return SimpleNamespace(
        contentDigest=_DIGEST,
        skillVersion=skill_version,
        status=SkillScanResultStatus.SUCCEEDED,
        maxSeverity=ScanSeverity.HIGH,
        severityCounts={ScanSeverity.HIGH: 1},
        tools=tools,
        scannedAt=datetime.now(UTC),
    )


def _job(*, status, summary=None) -> SimpleNamespace:
    return SimpleNamespace(
        skillId=PydanticObjectId(),
        status=status,
        rerequested=False,
        triggerType=SkillScanTriggerType.AUTO,
        requestedBy=None,
        requestedAt=datetime.now(UTC),
        startedAt=None,
        finishedAt=None,
        attemptCount=0,
        errorCode=None,
        summary=summary,
    )


def _tool_summary(tool: str, fingerprint: str) -> SimpleNamespace:
    return SimpleNamespace(
        tool=tool,
        toolFingerprint=fingerprint,
        status=SkillScanResultStatus.SUCCEEDED,
        statusReason=None,
    )


def _result(tool: str, fingerprint: str) -> SimpleNamespace:
    return SimpleNamespace(
        tool=tool,
        toolVersion="1.0.0",
        toolFingerprint=fingerprint,
        status=SkillScanResultStatus.SUCCEEDED,
        statusReason=None,
        findings=[],
        findingsTruncated=False,
        llm=None,
        usage=None,
        toolMetadata={},
        errorMessage=None,
        durationMs=12,
        updatedAt=datetime.now(UTC),
    )


# -----------------------------
# pure helpers
# -----------------------------


def test_scan_status_maps_done_to_completed_and_no_job_to_not_scanned():
    assert _scan_status(None) == "not_scanned"
    assert _scan_status(_job(status=SkillScanJobStatus.DONE)) == "completed"
    assert _scan_status(_job(status=SkillScanJobStatus.PENDING)) == "pending"
    assert _scan_status(_job(status=SkillScanJobStatus.FAILED)) == "failed"


def test_result_outdated_rules():
    fps = {"t": "sha256:current"}
    assert _result_outdated("t", "sha256:current", fps) is False
    assert _result_outdated("t", "sha256:old", fps) is True  # fingerprint changed
    assert _result_outdated("gone", "sha256:x", fps) is True  # tool removed
    assert _result_outdated("t", "sha256:old", None) is False  # no toolset published


# -----------------------------
# get_scan
# -----------------------------


@pytest.mark.asyncio
@patch("registry.services.skill_scan_service.current_tool_fingerprints", new_callable=AsyncMock)
@patch("registry.services.skill_scan_service.SkillScanJob")
@patch("registry.services.skill_scan_service.Skill")
async def test_get_scan_not_scanned_when_no_job(mock_skill_cls, mock_job_cls, mock_fps):
    mock_skill_cls.find_one = AsyncMock(return_value=_skill())
    mock_job_cls.find_one = AsyncMock(return_value=None)
    mock_fps.return_value = {"t": "sha256:current"}

    resp = await SkillScanService(_acl()).get_scan(PydanticObjectId(), _USER_ID)

    assert resp.scanStatus == "not_scanned"
    assert resp.job is None and resp.summary is None and resp.results == []
    assert resp.stale is False and resp.outdatedTool is False


@pytest.mark.asyncio
@patch("registry.services.skill_scan_service.current_tool_fingerprints", new_callable=AsyncMock)
@patch("registry.services.skill_scan_service.SkillScanResult")
@patch("registry.services.skill_scan_service.SkillScanJob")
@patch("registry.services.skill_scan_service.Skill")
async def test_get_scan_completed_stale_and_outdated(mock_skill_cls, mock_job_cls, mock_result_cls, mock_fps):
    skill = _skill(version=3)
    mock_skill_cls.find_one = AsyncMock(return_value=skill)
    job = _job(
        status=SkillScanJobStatus.DONE,
        summary=_summary(skill_version=2, tools=[_tool_summary("t", "sha256:old")]),
    )
    mock_job_cls.find_one = AsyncMock(return_value=job)
    query = MagicMock()
    query.to_list = AsyncMock(return_value=[_result("t", "sha256:old")])
    mock_result_cls.find.return_value = query
    mock_fps.return_value = {"t": "sha256:current"}

    resp = await SkillScanService(_acl()).get_scan(skill.id, _USER_ID)

    assert resp.scanStatus == "completed"
    assert resp.stale is True  # summary.skillVersion 2 != skill.version 3
    assert resp.outdatedTool is True  # result fingerprint no longer matches
    assert len(resp.results) == 1 and resp.results[0].outdatedTool is True


@pytest.mark.asyncio
@patch("registry.services.skill_scan_service.Skill")
async def test_get_scan_404_for_unknown_skill(mock_skill_cls):
    mock_skill_cls.find_one = AsyncMock(return_value=None)
    with pytest.raises(HTTPException) as exc:
        await SkillScanService(_acl()).get_scan(PydanticObjectId(), _USER_ID)
    assert exc.value.status_code == 404


# -----------------------------
# request_scan (200 vs 202)
# -----------------------------


@pytest.mark.asyncio
@patch("registry.services.skill_scan_service.enqueue_skill_scan", new_callable=AsyncMock)
@patch("registry.services.skill_scan_service.tools_needing_scan", new_callable=AsyncMock)
@patch("registry.services.skill_scan_service.current_tool_fingerprints", new_callable=AsyncMock)
@patch("registry.services.skill_scan_service.compute_content_digest", return_value=_DIGEST)
@patch("registry.services.skill_scan_service.SkillScanJob")
@patch("registry.services.skill_scan_service.SkillFile")
@patch("registry.services.skill_scan_service.Skill")
async def test_request_scan_returns_200_when_nothing_to_do(
    mock_skill_cls, mock_file_cls, mock_job_cls, _digest, mock_fps, mock_needing, mock_enqueue
):
    skill = _skill()
    mock_skill_cls.find_one = AsyncMock(return_value=skill)
    files_q = MagicMock()
    files_q.to_list = AsyncMock(return_value=[])
    mock_file_cls.find.return_value = files_q
    mock_job_cls.find_one = AsyncMock(return_value=None)
    mock_fps.return_value = {"t": "sha256:current"}
    mock_needing.return_value = []  # every tool already has a result

    _resp, queued = await SkillScanService(_acl()).request_scan(skill.id, _USER_ID)

    assert queued is False
    mock_enqueue.assert_not_awaited()


@pytest.mark.asyncio
@patch("registry.services.skill_scan_service.enqueue_skill_scan", new_callable=AsyncMock)
@patch("registry.services.skill_scan_service.tools_needing_scan", new_callable=AsyncMock)
@patch("registry.services.skill_scan_service.current_tool_fingerprints", new_callable=AsyncMock)
@patch("registry.services.skill_scan_service.compute_content_digest", return_value=_DIGEST)
@patch("registry.services.skill_scan_service.SkillScanJob")
@patch("registry.services.skill_scan_service.SkillFile")
@patch("registry.services.skill_scan_service.Skill")
async def test_request_scan_returns_202_and_enqueues_manual(
    mock_skill_cls, mock_file_cls, mock_job_cls, _digest, mock_fps, mock_needing, mock_enqueue
):
    skill = _skill()
    mock_skill_cls.find_one = AsyncMock(return_value=skill)
    files_q = MagicMock()
    files_q.to_list = AsyncMock(return_value=[])
    mock_file_cls.find.return_value = files_q
    mock_job_cls.find_one = AsyncMock(return_value=None)
    mock_fps.return_value = {"t": "sha256:current"}
    mock_needing.return_value = ["t"]  # a tool needs scanning

    _resp, queued = await SkillScanService(_acl()).request_scan(skill.id, _USER_ID)

    assert queued is True
    mock_enqueue.assert_awaited_once()
    assert mock_enqueue.await_args.kwargs["trigger"] == SkillScanTriggerType.MANUAL
    assert mock_enqueue.await_args.kwargs["requested_by"] == _USER_ID


@pytest.mark.asyncio
@patch("registry.services.skill_scan_service.enqueue_skill_scan", new_callable=AsyncMock)
@patch("registry.services.skill_scan_service.tools_needing_scan", new_callable=AsyncMock)
@patch("registry.services.skill_scan_service.current_tool_fingerprints", new_callable=AsyncMock)
@patch("registry.services.skill_scan_service.compute_content_digest", return_value=_DIGEST)
@patch("registry.services.skill_scan_service.SkillScanJob")
@patch("registry.services.skill_scan_service.SkillFile")
@patch("registry.services.skill_scan_service.Skill")
async def test_request_scan_treats_concurrent_duplicate_as_queued(
    mock_skill_cls, mock_file_cls, mock_job_cls, _digest, mock_fps, mock_needing, mock_enqueue
):
    # A concurrent request inserts the job first; skillId is unique, so enqueue raises
    # DuplicateKeyError. That collision is the queued outcome we wanted, not a 500.
    skill = _skill()
    mock_skill_cls.find_one = AsyncMock(return_value=skill)
    files_q = MagicMock()
    files_q.to_list = AsyncMock(return_value=[])
    mock_file_cls.find.return_value = files_q
    mock_job_cls.find_one = AsyncMock(return_value=None)
    mock_fps.return_value = None  # no toolset -> always queue
    mock_enqueue.side_effect = DuplicateKeyError("E11000 duplicate key")

    _resp, queued = await SkillScanService(_acl()).request_scan(skill.id, _USER_ID)

    assert queued is True
    mock_enqueue.assert_awaited_once()


@pytest.mark.asyncio
@patch("registry.services.skill_scan_service.enqueue_skill_scan", new_callable=AsyncMock)
@patch("registry.services.skill_scan_service.tools_needing_scan", new_callable=AsyncMock)
@patch("registry.services.skill_scan_service.current_tool_fingerprints", new_callable=AsyncMock)
@patch("registry.services.skill_scan_service.compute_content_digest", return_value=_DIGEST)
@patch("registry.services.skill_scan_service.SkillScanJob")
@patch("registry.services.skill_scan_service.SkillFile")
@patch("registry.services.skill_scan_service.Skill")
async def test_request_scan_always_queues_when_no_toolset_published(
    mock_skill_cls, mock_file_cls, mock_job_cls, _digest, mock_fps, mock_needing, mock_enqueue
):
    skill = _skill()
    mock_skill_cls.find_one = AsyncMock(return_value=skill)
    files_q = MagicMock()
    files_q.to_list = AsyncMock(return_value=[])
    mock_file_cls.find.return_value = files_q
    mock_job_cls.find_one = AsyncMock(return_value=None)
    mock_fps.return_value = None  # no toolset -> always queue

    _resp, queued = await SkillScanService(_acl()).request_scan(skill.id, _USER_ID)

    assert queued is True
    mock_enqueue.assert_awaited_once()
    mock_needing.assert_not_awaited()


# -----------------------------
# summaries_for (one query for the whole page)
# -----------------------------


@pytest.mark.asyncio
@patch("registry.services.skill_scan_service.current_tool_fingerprints", new_callable=AsyncMock)
@patch("registry.services.skill_scan_service.SkillScanJob")
async def test_summaries_for_batches_one_query_and_maps_missing_to_not_scanned(mock_job_cls, mock_fps):
    scanned = _skill(version=5)
    unscanned = _skill()
    job = _job(
        status=SkillScanJobStatus.DONE,
        summary=_summary(skill_version=5, tools=[_tool_summary("t", "sha256:current")]),
    )
    job.skillId = scanned.id
    jobs_q = MagicMock()
    jobs_q.to_list = AsyncMock(return_value=[job])
    mock_job_cls.find.return_value = jobs_q
    mock_fps.return_value = {"t": "sha256:current"}

    briefs = await SkillScanService(_acl()).summaries_for([scanned, unscanned])

    mock_job_cls.find.assert_called_once()  # one skill_scan_jobs query regardless of page size
    assert briefs[scanned.id].scanStatus == "completed"
    assert briefs[scanned.id].stale is False and briefs[scanned.id].outdatedTool is False
    assert briefs[unscanned.id].scanStatus == "not_scanned"
