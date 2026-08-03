import logging
from unittest.mock import AsyncMock

import pytest

from registry_pkgs.models.workflow import WorkflowRun
from registry_pkgs.workflows.hitl.cancellation import MongoBackedCancellationManager


@pytest.mark.unit
class TestAisCancelled:
    @pytest.mark.asyncio
    async def test_non_objectid_run_id_returns_false(self):
        mgr = MongoBackedCancellationManager()
        result = await mgr.ais_cancelled("8f96812e-1234-5678-abcd-ef0123456789")
        assert result is False

    @pytest.mark.asyncio
    async def test_non_objectid_run_id_logs_at_debug(self, caplog: pytest.LogCaptureFixture):
        mgr = MongoBackedCancellationManager()
        with caplog.at_level(logging.DEBUG, logger="registry_pkgs.workflows.hitl.cancellation"):
            await mgr.ais_cancelled("not-a-valid-objectid")

        matching = [r for r in caplog.records if "ais_cancelled: invalid run_id" in r.message]
        assert len(matching) == 1
        assert matching[0].levelno == logging.DEBUG

    @pytest.mark.asyncio
    async def test_valid_objectid_queries_mongo(self, monkeypatch: pytest.MonkeyPatch):
        from beanie import PydanticObjectId

        oid = PydanticObjectId()
        mgr = MongoBackedCancellationManager()

        class _FieldExpr:
            def __init__(self, name: str):
                self.name = name

            def __eq__(self, other):
                return (self.name, "==", other)

        monkeypatch.setattr(WorkflowRun, "id", _FieldExpr("id"), raising=False)
        mock_find = AsyncMock(return_value=None)
        monkeypatch.setattr(WorkflowRun, "find_one", mock_find)
        result = await mgr.ais_cancelled(str(oid))

        assert result is False
        mock_find.assert_awaited_once()
