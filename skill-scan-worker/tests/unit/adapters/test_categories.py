"""Coverage of the pinned vendor category enums.

The value lists below are hard-coded from the pinned tool sources so a version bump that adds a category
fails this test instead of silently mapping the new value to ``other``. Update the list and the mapping
table together when bumping a tool.
"""

from skill_scan_worker.adapters import categories

from registry_pkgs.models.enums import ScanFindingCategory, ScanSeverity

# skill_scanner/core/models.py :: ThreatCategory, cisco-ai-skill-scanner 2.3.2
_CISCO_THREAT_CATEGORIES = [
    "prompt_injection",
    "data_exfiltration",
    "command_injection",
    "malware",
    "hardcoded_secrets",
    "obfuscation",
    "unicode_steganography",
    "supply_chain_attack",
    "transitive_trust_abuse",
    "unauthorized_tool_use",
    "autonomy_abuse",
    "tool_chaining_abuse",
    "harmful_content",
    "social_engineering",
    "skill_discovery_abuse",
    "resource_abuse",
    "policy_violation",
]

# nodes/analyzers/pattern_defaults.py :: PatternCategory + extra category strings, SkillSpector v2.13.3
_SKILLSPECTOR_CATEGORIES = [
    "Prompt Injection",
    "Memory Poisoning",
    "MCP Tool Poisoning",
    "Anti-Refusal",
    "Data Exfiltration",
    "System Prompt Leakage",
    "Server-Side Request Forgery",
    "Output Handling",
    "Insecure Deserialization",
    "YARA Match",
    "Agent Snooping",
    "analysis-evasion",
    "Supply Chain",
    "MCP Rug Pull",
    "Privilege Escalation",
    "Excessive Agency",
    "Tool Misuse",
    "Rogue Agent",
    "MCP Least Privilege",
    "Trigger Abuse",
    "Security",
]


def test_every_cisco_category_is_mapped() -> None:
    for value in _CISCO_THREAT_CATEGORIES:
        assert categories.map_cisco_category(value) in ScanFindingCategory
        assert value.lower() in categories._CISCO_CATEGORY, f"unmapped Cisco category: {value}"


def test_every_skillspector_category_is_mapped() -> None:
    for value in _SKILLSPECTOR_CATEGORIES:
        assert categories.map_skillspector_category(value) in ScanFindingCategory
        assert value.lower() in categories._SKILLSPECTOR_CATEGORY, f"unmapped SkillSpector category: {value}"


def test_category_mapping_is_case_insensitive() -> None:
    assert categories.map_cisco_category("PROMPT_INJECTION") == ScanFindingCategory.PROMPT_INJECTION
    assert categories.map_skillspector_category("data exfiltration") == ScanFindingCategory.DATA_EXFILTRATION


def test_unknown_category_maps_to_other() -> None:
    assert categories.map_cisco_category("brand_new_thing") == ScanFindingCategory.OTHER
    assert categories.map_skillspector_category(None) == ScanFindingCategory.OTHER


def test_parse_severity() -> None:
    assert categories.parse_severity("CRITICAL", tool="cisco") == ScanSeverity.CRITICAL
    assert categories.parse_severity("high", tool="cisco") == ScanSeverity.HIGH
    assert categories.parse_severity("nonsense", tool="cisco") == ScanSeverity.INFO
    assert categories.parse_severity(None, tool="cisco") == ScanSeverity.INFO


def test_coerce_int() -> None:
    assert categories.coerce_int(14) == 14
    assert categories.coerce_int(14.0) == 14
    assert categories.coerce_int("14") == 14
    assert categories.coerce_int("14-18") is None
    assert categories.coerce_int("L14") is None
    assert categories.coerce_int(None) is None
    assert categories.coerce_int(True) is None


def test_parse_confidence() -> None:
    assert categories.parse_confidence(0.5) == 0.5
    assert categories.parse_confidence(1) == 1.0
    assert categories.parse_confidence(1.5) is None
    assert categories.parse_confidence(-0.1) is None
    assert categories.parse_confidence("0.5") is None
    assert categories.parse_confidence(True) is None
    assert categories.parse_confidence(None) is None
