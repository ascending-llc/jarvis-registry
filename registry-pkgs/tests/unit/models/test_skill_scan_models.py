from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from beanie import PydanticObjectId
from beanie.odm.settings.document import DocumentSettings

from registry_pkgs.models.enums import (
    SCAN_SEVERITY_RANK,
    ScanFindingCategory,
    ScanSeverity,
    SkillScanJobStatus,
    SkillScanResultStatus,
    SkillScanTriggerType,
)
from registry_pkgs.models.skill_scan_job import SkillScanJob
from registry_pkgs.models.skill_scan_result import (
    MAX_ERROR_MESSAGE_CHARS,
    MAX_FINDING_SNIPPET_CHARS,
    ScanFinding,
    SkillScanResult,
)


def _finding(**overrides) -> ScanFinding:
    values = {
        "tool": "cisco-skill-scanner",
        "severity": ScanSeverity.HIGH,
        "category": ScanFindingCategory.COMMAND_EXECUTION,
        "title": "reads /etc/shadow",
        "message": "the script reads a sensitive file",
    }
    values.update(overrides)
    return ScanFinding(**values)


def _result(**overrides) -> SkillScanResult:
    values = {
        "skillId": PydanticObjectId(),
        "contentDigest": "sha256:deadbeef",
        "skillVersion": 1,
        "tool": "cisco-skill-scanner",
        "toolVersion": "1.0.0",
        "toolFingerprint": "sha256:fp",
        "status": SkillScanResultStatus.SUCCEEDED,
    }
    values.update(overrides)
    # Patching get_settings lets model_validate run the capping validators without init_beanie / Mongo.
    with patch.object(SkillScanResult, "get_settings", return_value=DocumentSettings()):
        return SkillScanResult.model_validate(values)


def test_finding_snippet_is_capped():
    finding = _finding(snippet="x" * 2000)
    assert len(finding.snippet) == MAX_FINDING_SNIPPET_CHARS


def test_result_error_message_is_capped():
    result = _result(errorMessage="e" * 3000)
    assert len(result.errorMessage) == MAX_ERROR_MESSAGE_CHARS


def test_caps_apply_on_assignment():
    finding = _finding()
    finding.snippet = "x" * 2000
    assert len(finding.snippet) == MAX_FINDING_SNIPPET_CHARS
    result = _result()
    with patch.object(SkillScanResult, "get_settings", return_value=DocumentSettings()):
        result.errorMessage = "e" * 3000
    assert len(result.errorMessage) == MAX_ERROR_MESSAGE_CHARS


def test_short_values_are_untouched():
    finding = _finding(snippet="short")
    assert finding.snippet == "short"
    result = _result(errorMessage="boom")
    assert result.errorMessage == "boom"


def test_scan_job_save_is_lease_fenced():
    job = SkillScanJob.model_construct(
        skillId=PydanticObjectId(),
        triggerType=SkillScanTriggerType.AUTO,
        requestedAt=datetime.now(UTC),
    )
    with pytest.raises(TypeError):
        job.save()


def test_severity_rank_is_total_and_ordered():
    assert set(SCAN_SEVERITY_RANK) == set(ScanSeverity)
    assert SCAN_SEVERITY_RANK[ScanSeverity.CRITICAL] > SCAN_SEVERITY_RANK[ScanSeverity.INFO]


def test_enum_string_values():
    assert SkillScanJobStatus.SCANNING.value == "scanning"
    assert SkillScanResultStatus.PARTIAL.value == "partial"
    assert ScanSeverity.CRITICAL.value == "critical"
