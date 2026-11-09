from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from beanie import PydanticObjectId
from skill_scan_worker import finish as finish_module
from skill_scan_worker.finish import _worst_status

from registry_pkgs.database.leased_job import LeaseLostError
from registry_pkgs.models.enums import ScanFindingCategory, ScanSeverity, SkillScanResultStatus
from registry_pkgs.models.skill_scan_result import ScanFinding, SkillScanResult

_DIGEST = "sha256:d"


def _v(status: SkillScanResultStatus) -> str:
    return status.value


def test_empty_defaults_to_succeeded() -> None:
    assert _worst_status([]) == SkillScanResultStatus.SUCCEEDED


def test_failed_beats_everything() -> None:
    assert _worst_status([_v(SkillScanResultStatus.FAILED), _v(SkillScanResultStatus.PARTIAL)]) == (
        SkillScanResultStatus.FAILED
    )


def test_partial_beats_succeeded() -> None:
    assert _worst_status([_v(SkillScanResultStatus.PARTIAL), _v(SkillScanResultStatus.SUCCEEDED)]) == (
        SkillScanResultStatus.PARTIAL
    )


def test_skipped_only_when_all_skipped() -> None:
    assert _worst_status([_v(SkillScanResultStatus.SKIPPED), _v(SkillScanResultStatus.SKIPPED)]) == (
        SkillScanResultStatus.SKIPPED
    )


def test_skipped_plus_succeeded_is_succeeded() -> None:
    assert _worst_status([_v(SkillScanResultStatus.SKIPPED), _v(SkillScanResultStatus.SUCCEEDED)]) == (
        SkillScanResultStatus.SUCCEEDED
    )


# --- _build_summary / _write_results / finish_job (fake collection, no Mongo) ---


class _Cursor:
    def __init__(self, docs: list[dict]) -> None:
        self._docs = docs

    def __aiter__(self):
        async def _gen():
            for doc in self._docs:
                yield doc

        return _gen()


def _finding(severity: ScanSeverity) -> ScanFinding:
    return ScanFinding(tool="x", severity=severity, category=ScanFindingCategory.OTHER, title="t", message="m")


def _doc(tool: str, fingerprint: str, status: SkillScanResultStatus, findings, updated_at: datetime) -> dict:
    return SkillScanResult(
        skillId=PydanticObjectId(),
        contentDigest=_DIGEST,
        skillVersion=1,
        tool=tool,
        toolVersion="1",
        toolFingerprint=fingerprint,
        status=status,
        findings=findings,
        updatedAt=updated_at,
    ).model_dump(mode="python", exclude={"id"})


async def test_build_summary_prefers_current_fingerprint_then_newest(monkeypatch) -> None:
    now = datetime.now(UTC)
    docs = [
        # a: current-fingerprint result wins over a newer old-fingerprint one.
        _doc("a", "fp-a-new", SkillScanResultStatus.SUCCEEDED, [_finding(ScanSeverity.HIGH)], now - timedelta(hours=1)),
        _doc("a", "fp-a-old", SkillScanResultStatus.FAILED, [_finding(ScanSeverity.CRITICAL)], now),
        # b: no current-fingerprint result; the newest old one is used.
        _doc("b", "fp-b-old1", SkillScanResultStatus.FAILED, [], now - timedelta(hours=2)),
        _doc("b", "fp-b-old2", SkillScanResultStatus.PARTIAL, [_finding(ScanSeverity.LOW)] * 2, now),
        # z: not an enabled tool, so ignored.
        _doc("z", "fp-z", SkillScanResultStatus.FAILED, [_finding(ScanSeverity.CRITICAL)], now),
    ]
    collection = SimpleNamespace(find=lambda *_a, **_k: _Cursor(docs))
    monkeypatch.setattr(SkillScanResult, "get_pymongo_collection", classmethod(lambda cls: collection))
    monkeypatch.setattr(
        finish_module,
        "ENABLED_ADAPTERS",
        [SimpleNamespace(name="a"), SimpleNamespace(name="b"), SimpleNamespace(name="c")],
    )
    skill = SimpleNamespace(id=PydanticObjectId(), version=7)

    summary = await finish_module._build_summary(
        MagicMock(), skill, _DIGEST, {"a": "fp-a-new", "b": "fp-b-new", "c": "fp-c"}, now
    )

    assert [(t.tool, t.toolFingerprint) for t in summary.tools] == [("a", "fp-a-new"), ("b", "fp-b-old2")]
    assert summary.status == SkillScanResultStatus.PARTIAL
    assert summary.maxSeverity == ScanSeverity.HIGH
    assert summary.severityCounts == {ScanSeverity.HIGH: 1, ScanSeverity.LOW: 2}
    assert summary.skillVersion == 7


async def test_write_results_omits_null_fields(monkeypatch) -> None:
    collection = SimpleNamespace(replace_one=AsyncMock(), delete_many=AsyncMock())
    monkeypatch.setattr(SkillScanResult, "get_pymongo_collection", classmethod(lambda cls: collection))
    result = SkillScanResult(
        skillId=PydanticObjectId(),
        contentDigest=_DIGEST,
        skillVersion=1,
        tool="a",
        toolVersion="1",
        toolFingerprint="fp",
        status=SkillScanResultStatus.SUCCEEDED,
        findings=[_finding(ScanSeverity.LOW)],
    )

    await finish_module._write_results(MagicMock(), result.skillId, _DIGEST, {"a": "fp"}, [result])

    doc = collection.replace_one.await_args.args[1]
    assert {"statusReason", "llm", "usage", "errorMessage", "durationMs"}.isdisjoint(doc)
    assert "ruleId" not in doc["findings"][0]


async def test_finish_job_records_lease_lost(monkeypatch) -> None:
    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

        async def with_transaction(self, _callback):
            raise LeaseLostError("lost")

    client = SimpleNamespace(start_session=lambda: _Session())
    monkeypatch.setattr(finish_module.MongoDB, "get_client", classmethod(lambda cls: client))
    recorded = MagicMock()
    monkeypatch.setattr(finish_module, "record_job_finished", recorded)

    with pytest.raises(LeaseLostError):
        await finish_module.finish_job(MagicMock(), SimpleNamespace(id=PydanticObjectId(), version=1), _DIGEST, {}, [])

    recorded.assert_called_once_with("lease_lost")
