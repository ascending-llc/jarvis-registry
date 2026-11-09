from skill_scan_worker.adapters.base import ScannerAdapter
from skill_scan_worker.adapters.cisco import CiscoSkillScannerAdapter
from skill_scan_worker.adapters.skillspector import SkillSpectorAdapter

ENABLED_ADAPTERS: list[ScannerAdapter] = [CiscoSkillScannerAdapter(), SkillSpectorAdapter()]
