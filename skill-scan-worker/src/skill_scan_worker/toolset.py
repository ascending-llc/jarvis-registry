"""Publish the SkillScanToolset at startup so Registry knows which tools run and when a result is outdated."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from registry_pkgs.models.skill_scan_toolset import (
    SKILL_SCAN_TOOLSET_ID,
    SkillScanToolDescriptor,
    SkillScanToolset,
)
from skill_scan_worker.adapters import ENABLED_ADAPTERS

logger = logging.getLogger(__name__)


def validate_adapters() -> None:
    """Refuse to start on a broken adapter set, so a bad image never claims jobs."""
    if not ENABLED_ADAPTERS:
        raise RuntimeError("ENABLED_ADAPTERS is empty; refusing to start")
    names = [a.name for a in ENABLED_ADAPTERS]
    if len(names) != len(set(names)):
        raise RuntimeError(f"ENABLED_ADAPTERS has duplicate names: {names}")


async def publish_toolset(owner: str) -> None:
    """Upsert the singleton toolset from the enabled adapters. Validates the adapter set first."""
    validate_adapters()
    descriptor = SkillScanToolset(
        id=SKILL_SCAN_TOOLSET_ID,
        tools=[
            SkillScanToolDescriptor(tool=a.name, toolVersion=a.tool_version, staticFingerprint=a.static_fingerprint())
            for a in ENABLED_ADAPTERS
        ],
        publishedBy=owner,
        publishedAt=datetime.now(UTC),
    )
    await SkillScanToolset.get_pymongo_collection().replace_one(
        {"_id": SKILL_SCAN_TOOLSET_ID}, descriptor.model_dump(mode="python", by_alias=True), upsert=True
    )
    logger.info("Published skill-scan toolset with %d tool(s)", len(ENABLED_ADAPTERS))
