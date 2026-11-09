"""Tests for migration 0004: remove failure-written MCP initialization flags."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from registry_pkgs.migrations.versions import m0004_unset_failed_probe_requires_init as migration


@pytest.mark.asyncio
async def test_unsets_only_requires_init_for_failed_probes(caplog: pytest.LogCaptureFixture) -> None:
    collection = MagicMock(update_many=AsyncMock(return_value=MagicMock(matched_count=2, modified_count=1)))
    db = MagicMock()
    db.__getitem__.return_value = collection
    caplog.set_level(logging.INFO, logger=migration.__name__)

    await migration.up(db)

    db.__getitem__.assert_called_once_with("mcpservers")
    collection.update_many.assert_awaited_once_with(
        {
            "config.requiresInit": False,
            "lastError": {"$ne": None},
            "$or": [
                {"lastConnected": None},
                {"$expr": {"$gte": ["$lastError", "$lastConnected"]}},
            ],
        },
        {"$unset": {"config.requiresInit": ""}},
    )
    assert "Unset failed-probe requiresInit flags: matched=2 modified=1" in caplog.text
