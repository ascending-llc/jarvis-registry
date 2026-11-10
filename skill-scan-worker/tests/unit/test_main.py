from unittest.mock import AsyncMock, MagicMock

import pytest
from skill_scan_worker import main as main_module
from skill_scan_worker import toolset


async def test_empty_adapters_stop_startup_before_any_runner(monkeypatch) -> None:
    monkeypatch.setattr(toolset, "ENABLED_ADAPTERS", [])
    monkeypatch.setattr(main_module, "_initialize_telemetry", lambda: None)
    monkeypatch.setattr(main_module, "init_mongodb", AsyncMock())
    runner_cls = MagicMock()
    monkeypatch.setattr(main_module, "LeasedJobRunner", runner_cls)
    sweeper_cls = MagicMock()
    monkeypatch.setattr(main_module, "OrphanSweeper", sweeper_cls)

    # asyncio.run(main()) propagates this, so the process exits non-zero.
    with pytest.raises(RuntimeError, match="empty"):
        await main_module.main()

    runner_cls.assert_not_called()
    sweeper_cls.assert_not_called()
