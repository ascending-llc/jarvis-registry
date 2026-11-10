"""execute_job orchestration: real planning/content helpers, mocked Mongo access, and a recording process runner."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId
from skill_scan_worker import job as job_module
from skill_scan_worker.adapters.base import ToolOutcome, static_fingerprint_for
from skill_scan_worker.llm import ResolvedLLM
from skill_scan_worker.process import Invocation, ProcessResult

from registry_pkgs.models.enums import ModelSourceMode, SkillScanResultStatus, SkillScanStatusReason
from registry_pkgs.models.extended_skill import ExtendedSkill
from registry_pkgs.models.extended_skill_file import REGISTRY_SKILL_FILE_SOURCE, ExtendedSkillFile
from registry_pkgs.models.model_source import AwsBedrockModelConfig, ModelSource

pytestmark = pytest.mark.asyncio

_CHAT_FILE_SOURCE = "chat-storage"


class _Adapter:
    def __init__(self, name: str, *, llm_input_unbounded: bool) -> None:
        self.name = name
        self.tool_version = "1.0.0"
        self.llm_input_unbounded = llm_input_unbounded
        self.llm_seen: list[ResolvedLLM | None] = []

    def static_fingerprint(self) -> str:
        return static_fingerprint_for(self.name, self.tool_version, {})

    def build_invocation(self, skill_dir: Path, output_file: Path, llm: ResolvedLLM | None) -> Invocation:
        self.llm_seen.append(llm)
        return Invocation(argv=["unused"], output_file=output_file, llm_used=llm is not None)

    def parse(self, process: ProcessResult, *, llm_requested: bool) -> ToolOutcome:
        return ToolOutcome(SkillScanResultStatus.SUCCEEDED, None, [])


def _skill(*, created_by_registry: bool = True, file_count: int = 0, body: str = "hello") -> ExtendedSkill:
    skill = ExtendedSkill(
        name="s",
        description="d",
        body=body,
        author=PydanticObjectId(),
        authorName="a",
        createdByRegistry=created_by_registry,
        fileCount=file_count,
    )
    skill.id = PydanticObjectId()
    return skill


def _file(skill: ExtendedSkill, path: str, *, source: str = REGISTRY_SKILL_FILE_SOURCE) -> ExtendedSkillFile:
    body = b"data" if source == REGISTRY_SKILL_FILE_SOURCE else None
    return ExtendedSkillFile(
        skillId=skill.id, relativePath=path, source=source, mimeType="text/plain", bytes=4, isBinary=False, body=body
    )


def _bedrock_source() -> ModelSource:
    source = ModelSource(
        displayName="scan",
        mode=ModelSourceMode.CHAT,
        providerConfig=AwsBedrockModelConfig(awsRegion="us-east-1", modelIdOrArn="m", baseModelId="b"),
    )
    source.id = PydanticObjectId()
    return source


@pytest.fixture
def harness(monkeypatch, tmp_path: Path):
    """Patch every Mongo touchpoint of job.py; return the knobs and recorders the tests use."""
    h = SimpleNamespace(
        skill=None,
        files=[],
        source=None,
        pending=None,  # None => every enabled tool
        adapters=[_Adapter("a", llm_input_unbounded=False)],
        finish_job=AsyncMock(),
        delete_state=AsyncMock(),
        run_process=AsyncMock(),
        transition=AsyncMock(return_value=True),
    )

    async def _get(_skill_id):
        return h.skill

    async def _tools_needing_scan(_skill_id, _digest, fingerprints):
        return list(fingerprints) if h.pending is None else h.pending

    async def _run_tool_process(invocation, *, cwd, timeout, max_output_bytes):
        await h.run_process(invocation)
        return ProcessResult(0, False, invocation.output_file, False, "", 1)

    fake_file_model = SimpleNamespace(
        skillId=MagicMock(), find=lambda *_a, **_k: SimpleNamespace(to_list=AsyncMock(side_effect=lambda: h.files))
    )
    monkeypatch.setattr(job_module, "ExtendedSkill", SimpleNamespace(get=_get))
    monkeypatch.setattr(job_module, "ExtendedSkillFile", fake_file_model)
    monkeypatch.setattr(job_module, "resolve_skill_scan_model_source", AsyncMock(side_effect=lambda: h.source))
    monkeypatch.setattr(job_module, "tools_needing_scan", _tools_needing_scan)
    monkeypatch.setattr(job_module, "delete_skill_scan_state", h.delete_state)
    monkeypatch.setattr(job_module, "finish_job", h.finish_job)
    monkeypatch.setattr(job_module, "run_tool_process", _run_tool_process)
    monkeypatch.setattr(job_module.skill_scan_job_repository, "transition", h.transition)
    monkeypatch.setattr(job_module.settings, "work_dir", str(tmp_path))

    def _set_adapters(adapters):
        h.adapters = adapters
        monkeypatch.setattr(job_module, "ENABLED_ADAPTERS", adapters)

    h.set_adapters = _set_adapters
    _set_adapters(h.adapters)
    return h


def _job(skill_id: PydanticObjectId, attempt_count: int = 1) -> SimpleNamespace:
    return SimpleNamespace(skillId=skill_id, attemptCount=attempt_count)


def _finished_results(h) -> list:
    h.finish_job.assert_awaited_once()
    return h.finish_job.await_args.args[4]


async def test_all_tools_done_runs_no_process_and_finishes(harness) -> None:
    harness.skill = _skill()
    harness.pending = []
    await job_module.execute_job(_job(harness.skill.id), MagicMock())
    harness.run_process.assert_not_awaited()
    assert _finished_results(harness) == []


async def test_chat_created_skill_with_supporting_files_is_skipped(harness) -> None:
    harness.skill = _skill(created_by_registry=False, file_count=1)
    harness.files = [_file(harness.skill, "ref.md", source=_CHAT_FILE_SOURCE)]
    await job_module.execute_job(_job(harness.skill.id), MagicMock())
    harness.run_process.assert_not_awaited()
    [result] = _finished_results(harness)
    assert result.status == SkillScanResultStatus.SKIPPED
    assert result.statusReason == SkillScanStatusReason.CHAT_SUPPORTING_FILES_UNSUPPORTED


async def test_registry_skill_with_chat_added_file_is_skipped(harness) -> None:
    harness.skill = _skill(created_by_registry=True, file_count=2)
    harness.files = [_file(harness.skill, "ok.md"), _file(harness.skill, "chat.md", source=_CHAT_FILE_SOURCE)]
    await job_module.execute_job(_job(harness.skill.id), MagicMock())
    harness.run_process.assert_not_awaited()
    [result] = _finished_results(harness)
    assert result.status == SkillScanResultStatus.SKIPPED
    assert result.statusReason == SkillScanStatusReason.CHAT_SUPPORTING_FILES_UNSUPPORTED


async def test_oversized_skill_runs_unbounded_adapter_without_llm(harness, monkeypatch) -> None:
    unbounded = _Adapter("unbounded", llm_input_unbounded=True)
    bounded = _Adapter("bounded", llm_input_unbounded=False)
    harness.set_adapters([unbounded, bounded])
    harness.skill = _skill(body="x" * 100)
    harness.source = _bedrock_source()
    monkeypatch.setattr(job_module.settings, "llm_max_text_bytes", 10)

    await job_module.execute_job(_job(harness.skill.id), MagicMock())

    assert unbounded.llm_seen == [None]
    assert len(bounded.llm_seen) == 1 and bounded.llm_seen[0] is not None
    by_tool = {r.tool: r for r in _finished_results(harness)}
    assert by_tool["unbounded"].status == SkillScanResultStatus.PARTIAL
    assert by_tool["unbounded"].statusReason == SkillScanStatusReason.LLM_SKIPPED_SIZE_LIMIT
    assert by_tool["bounded"].status == SkillScanResultStatus.SUCCEEDED
    assert harness.run_process.await_count == 2


async def test_no_llm_configured_is_not_reported_as_size_limit(harness, monkeypatch) -> None:
    unbounded = _Adapter("unbounded", llm_input_unbounded=True)
    harness.set_adapters([unbounded])
    harness.skill = _skill(body="x" * 100)
    monkeypatch.setattr(job_module.settings, "llm_max_text_bytes", 10)

    await job_module.execute_job(_job(harness.skill.id), MagicMock())

    [result] = _finished_results(harness)
    assert result.statusReason != SkillScanStatusReason.LLM_SKIPPED_SIZE_LIMIT


async def test_missing_skill_deletes_state_and_records_nothing(harness) -> None:
    skill_id = PydanticObjectId()
    await job_module.execute_job(_job(skill_id), MagicMock())
    harness.delete_state.assert_awaited_once_with(skill_id, session=None)
    harness.finish_job.assert_not_awaited()
    harness.run_process.assert_not_awaited()


async def test_run_dir_is_removed(harness, tmp_path: Path) -> None:
    harness.skill = _skill()
    await job_module.execute_job(_job(harness.skill.id), MagicMock())
    assert list(tmp_path.iterdir()) == []


async def test_cancel_during_initial_load_restores_the_attempt(harness, monkeypatch) -> None:
    async def _cancelled(_skill_id):
        raise asyncio.CancelledError

    monkeypatch.setattr(job_module, "ExtendedSkill", SimpleNamespace(get=_cancelled))
    lease = MagicMock()
    with pytest.raises(asyncio.CancelledError):
        await job_module.execute_job(_job(PydanticObjectId(), attempt_count=2), lease)

    harness.transition.assert_awaited_once()
    assert harness.transition.await_args.args == (lease, {"status": "pending", "attemptCount": 1})
    assert harness.transition.await_args.kwargs == {"release": True}
