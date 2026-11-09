"""Normalize each scanner's native category and severity into the shared finding schema.

The category tables map a tool's native category to ``ScanFindingCategory``. Anything absent maps to
``other`` (logged at debug). A unit test enumerates every value of the pinned vendor enums against these
tables, so a version bump that adds a category fails loudly.
"""

from __future__ import annotations

import logging

from registry_pkgs.models.enums import ScanFindingCategory, ScanSeverity

logger = logging.getLogger(__name__)

# Cisco ThreatCategory -> ours (skill_scanner/core/models.py ThreatCategory at 2.3.2). Keys are lowercased.
_CISCO_CATEGORY: dict[str, ScanFindingCategory] = {
    "prompt_injection": ScanFindingCategory.PROMPT_INJECTION,
    "data_exfiltration": ScanFindingCategory.DATA_EXFILTRATION,
    "command_injection": ScanFindingCategory.COMMAND_EXECUTION,
    "malware": ScanFindingCategory.COMMAND_EXECUTION,
    "hardcoded_secrets": ScanFindingCategory.CREDENTIAL_ACCESS,
    "obfuscation": ScanFindingCategory.OBFUSCATION,
    "unicode_steganography": ScanFindingCategory.OBFUSCATION,
    "supply_chain_attack": ScanFindingCategory.SUPPLY_CHAIN,
    "transitive_trust_abuse": ScanFindingCategory.SUPPLY_CHAIN,
    "unauthorized_tool_use": ScanFindingCategory.EXCESSIVE_PERMISSIONS,
    "autonomy_abuse": ScanFindingCategory.EXCESSIVE_PERMISSIONS,
    "tool_chaining_abuse": ScanFindingCategory.EXCESSIVE_PERMISSIONS,
    "harmful_content": ScanFindingCategory.HARMFUL_CONTENT,
    "social_engineering": ScanFindingCategory.SOCIAL_ENGINEERING,
    "skill_discovery_abuse": ScanFindingCategory.SOCIAL_ENGINEERING,
    "resource_abuse": ScanFindingCategory.OTHER,
    "policy_violation": ScanFindingCategory.OTHER,
}

# SkillSpector PatternCategory + extra category strings (nodes/analyzers/pattern_defaults.py at v2.13.3).
# Keys lowercased; SkillSpector uses human-readable names like "Prompt Injection".
_SKILLSPECTOR_CATEGORY: dict[str, ScanFindingCategory] = {
    "prompt injection": ScanFindingCategory.PROMPT_INJECTION,
    "memory poisoning": ScanFindingCategory.PROMPT_INJECTION,
    "mcp tool poisoning": ScanFindingCategory.PROMPT_INJECTION,
    "anti-refusal": ScanFindingCategory.PROMPT_INJECTION,
    "data exfiltration": ScanFindingCategory.DATA_EXFILTRATION,
    "system prompt leakage": ScanFindingCategory.DATA_EXFILTRATION,
    "server-side request forgery": ScanFindingCategory.DATA_EXFILTRATION,
    "output handling": ScanFindingCategory.COMMAND_EXECUTION,
    "insecure deserialization": ScanFindingCategory.COMMAND_EXECUTION,
    "yara match": ScanFindingCategory.COMMAND_EXECUTION,
    "agent snooping": ScanFindingCategory.CREDENTIAL_ACCESS,
    "analysis-evasion": ScanFindingCategory.OBFUSCATION,
    "supply chain": ScanFindingCategory.SUPPLY_CHAIN,
    "mcp rug pull": ScanFindingCategory.SUPPLY_CHAIN,
    "privilege escalation": ScanFindingCategory.EXCESSIVE_PERMISSIONS,
    "excessive agency": ScanFindingCategory.EXCESSIVE_PERMISSIONS,
    "tool misuse": ScanFindingCategory.EXCESSIVE_PERMISSIONS,
    "rogue agent": ScanFindingCategory.EXCESSIVE_PERMISSIONS,
    "mcp least privilege": ScanFindingCategory.EXCESSIVE_PERMISSIONS,
    "trigger abuse": ScanFindingCategory.SOCIAL_ENGINEERING,
    "security": ScanFindingCategory.OTHER,
}

_SEVERITY_BY_NAME = {s.value: s for s in ScanSeverity}
# Severity names already warned about, so an unknown value logs once per tool, not per finding.
_severity_warned: set[str] = set()


def map_cisco_category(native: str | None) -> ScanFindingCategory:
    return _map_category(_CISCO_CATEGORY, native, "cisco")


def map_skillspector_category(native: str | None) -> ScanFindingCategory:
    return _map_category(_SKILLSPECTOR_CATEGORY, native, "skillspector")


def _map_category(table: dict[str, ScanFindingCategory], native: str | None, tool: str) -> ScanFindingCategory:
    if native is None:
        return ScanFindingCategory.OTHER
    mapped = table.get(native.strip().lower())
    if mapped is None:
        logger.debug("%s: unmapped category %r -> other", tool, native)
        return ScanFindingCategory.OTHER
    return mapped


def parse_severity(raw: str | None, *, tool: str) -> ScanSeverity:
    """Lowercase a tool's severity into ``ScanSeverity``. An unknown value maps to ``info``, logged once."""
    if raw is not None:
        severity = _SEVERITY_BY_NAME.get(raw.strip().lower())
        if severity is not None:
            return severity
    key = f"{tool}:{raw}"
    if key not in _severity_warned:
        _severity_warned.add(key)
        logger.warning("%s: unknown severity %r -> info", tool, raw)
    return ScanSeverity.INFO


def parse_confidence(value: object) -> float | None:
    """Return ``value`` as a float only when it is a number in [0, 1]; otherwise ``None``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if 0.0 <= number <= 1.0 else None


def coerce_int(value: object) -> int | None:
    """Best-effort int from a tool-supplied value (line number, token count): ``None`` when it is not a
    number or numeric string. Keeps one malformed field from failing the whole report parse."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None
