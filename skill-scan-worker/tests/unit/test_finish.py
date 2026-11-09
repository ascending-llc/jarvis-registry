from skill_scan_worker.finish import _worst_status

from registry_pkgs.models.enums import SkillScanResultStatus


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
