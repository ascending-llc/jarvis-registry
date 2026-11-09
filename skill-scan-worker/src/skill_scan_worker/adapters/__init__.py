"""The static list of enabled scanner adapters, changed only by redeploy (D3).

Empty here; fills it with the Cisco and SkillSpector adapters. Startup validation (``toolset.py``)
refuses to run with an empty list, so a broken image never claims jobs.
"""

from skill_scan_worker.adapters.base import ScannerAdapter

ENABLED_ADAPTERS: list[ScannerAdapter] = []
