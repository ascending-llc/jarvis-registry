import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from registry_pkgs.models.enums import WorkflowDirective
from registry_pkgs.models.workflow import WorkflowRun
from registry_pkgs.workflows.hitl.cancellation import MongoBackedCancellationManager
from registry_pkgs.workflows.run_repository import NON_TERMINAL_RUN_STATUSES


def _stub_collection(monkeypatch: pytest.MonkeyPatch, modified_count: int) -> AsyncMock:
    """Stub WorkflowRun's pymongo collection so acancel_run's update_one needs no live DB."""
    collection = AsyncMock()
    collection.update_one.return_value = SimpleNamespace(modified_count=modified_count)
    monkeypatch.setattr(WorkflowRun, "get_pymongo_collection", classmethod(lambda cls: collection))
    return collection


@pytest.mark.unit
class TestAcancelRun:
    @pytest.mark.asyncio
    async def test_arms_cancel_only_while_non_terminal(self, monkeypatch: pytest.MonkeyPatch):
        collection = _stub_collection(monkeypatch, modified_count=1)

        ok = await MongoBackedCancellationManager().acancel_run(str(PydanticObjectId()))

        assert ok is True
        flt, update = collection.update_one.await_args.args
        # The guard must constrain the write to non-terminal statuses, and only arm CANCEL —
        # this fails loudly if someone drops the status filter, the whole point of the change.
        assert sorted(flt["status"]["$in"]) == sorted(s.value for s in NON_TERMINAL_RUN_STATUSES)
        assert update["$set"]["pending_directive"] == WorkflowDirective.CANCEL.value

    @pytest.mark.asyncio
    async def test_returns_false_when_run_is_terminal(self, monkeypatch: pytest.MonkeyPatch):
        # The non-terminal status filter matches nothing on a CANCELLED/COMPLETED run, so
        # _finalize_cancel cannot re-arm a stale CANCEL over a terminal state.
        _stub_collection(monkeypatch, modified_count=0)

        ok = await MongoBackedCancellationManager().acancel_run(str(PydanticObjectId()))

        assert ok is False


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
