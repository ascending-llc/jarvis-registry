from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from beanie import PydanticObjectId

from registry_pkgs.models.enums import WorkflowRunStatus
from registry_pkgs.workflows import schedule_repository
from registry_pkgs.workflows.schedule_repository import WorkflowScheduleRepository


class _AsyncContext:
    def __init__(self, value: object) -> None:
        self._value = value

    async def __aenter__(self) -> object:
        return self._value

    async def __aexit__(self, *_args: object) -> bool:
        return False


class _Session:
    async def start_transaction(self) -> _AsyncContext:
        return _AsyncContext(self)


def _repository(
    monkeypatch: pytest.MonkeyPatch,
    collection: SimpleNamespace,
    client: object | None = None,
) -> WorkflowScheduleRepository:
    monkeypatch.setattr(
        schedule_repository.WorkflowSchedule,
        "get_settings",
        lambda: SimpleNamespace(name="workflow_schedules"),
    )
    database = SimpleNamespace(
        get_collection=lambda _name: collection,
        client=client,
    )
    return WorkflowScheduleRepository(database)


@pytest.mark.asyncio
async def test_claim_due_uses_atomic_fenced_update(monkeypatch: pytest.MonkeyPatch) -> None:
    collection = SimpleNamespace(find_one_and_update=AsyncMock(return_value={"_id": "claimed"}))
    repository = _repository(monkeypatch, collection)
    claimed = object()
    monkeypatch.setattr(schedule_repository.WorkflowSchedule, "model_validate", lambda _document: claimed)

    result = await repository.claim_due(lease_seconds=120)

    assert result is claimed
    query, update = collection.find_one_and_update.await_args.args
    assert query["enabled"] is True
    assert query["next_run_at"]["$lte"].tzinfo is UTC
    assert {"locked_until": None} in query["$or"]
    assert update["$set"]["locked_until"] > datetime.now(UTC)
    assert update["$set"]["lease_token"]
    assert collection.find_one_and_update.await_args.kwargs["sort"] == [("next_run_at", 1)]


@pytest.mark.asyncio
async def test_claim_due_returns_none_when_no_schedule_is_due(monkeypatch: pytest.MonkeyPatch) -> None:
    collection = SimpleNamespace(find_one_and_update=AsyncMock(return_value=None))
    repository = _repository(monkeypatch, collection)

    assert await repository.claim_due(lease_seconds=120) is None


@pytest.mark.asyncio
async def test_peek_next_deadline_reads_earliest_claimable_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    deadline = datetime.now(UTC) + timedelta(minutes=5)
    collection = SimpleNamespace(find_one=AsyncMock(return_value={"next_run_at": deadline}))
    repository = _repository(monkeypatch, collection)

    result = await repository.peek_next_deadline()

    assert result == deadline
    query = collection.find_one.await_args.args[0]
    assert query["enabled"] is True
    assert query["next_run_at"] == {"$ne": None}
    assert collection.find_one.await_args.kwargs["projection"] == {"next_run_at": 1}


@pytest.mark.asyncio
async def test_renew_and_load_claim_use_same_fencing_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    schedule_id = PydanticObjectId()
    claimed = object()
    collection = SimpleNamespace(
        update_one=AsyncMock(return_value=SimpleNamespace(matched_count=1)),
        find_one=AsyncMock(return_value={"_id": schedule_id}),
    )
    repository = _repository(monkeypatch, collection)
    monkeypatch.setattr(schedule_repository.WorkflowSchedule, "model_validate", lambda _document: claimed)

    renewed = await repository.renew_claim(schedule_id, "lease-1", 120)
    loaded = await repository.load_claim(schedule_id, "lease-1")

    assert renewed is True
    assert loaded is claimed
    assert collection.update_one.await_args.args[0] == {"_id": schedule_id, "lease_token": "lease-1"}
    assert collection.find_one.await_args.args[0] == {
        "_id": schedule_id,
        "lease_token": "lease-1",
        "enabled": True,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("matched_count", [0, 1])
async def test_advance_and_insert_run_is_transactional_and_fenced(
    monkeypatch: pytest.MonkeyPatch,
    matched_count: int,
) -> None:
    session = _Session()
    client = SimpleNamespace(start_session=lambda: _AsyncContext(session))
    collection = SimpleNamespace(update_one=AsyncMock(return_value=SimpleNamespace(matched_count=matched_count)))
    repository = _repository(monkeypatch, collection, client)
    claimed = SimpleNamespace(
        id=PydanticObjectId(),
        lease_token="lease-1",
        cron_expression="0 2 * * *",
        timezone="UTC",
    )
    run = SimpleNamespace(insert=AsyncMock())
    next_run_at = datetime.now(UTC) + timedelta(days=1)

    inserted = await repository.advance_and_insert_run(claimed, run, next_run_at)

    assert inserted is bool(matched_count)
    query = collection.update_one.await_args.args[0]
    assert query["lease_token"] == "lease-1"
    assert collection.update_one.await_args.kwargs["session"] is session
    if matched_count:
        run.insert.assert_awaited_once_with(session=session)
    else:
        run.insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_disable_and_finish_claim_are_fenced(monkeypatch: pytest.MonkeyPatch) -> None:
    schedule_id = PydanticObjectId()
    run_id = PydanticObjectId()
    collection = SimpleNamespace(update_one=AsyncMock(return_value=SimpleNamespace(matched_count=1)))
    repository = _repository(monkeypatch, collection)

    disabled = await repository.disable_claim(schedule_id, "lease-1")
    disable_call = collection.update_one.await_args
    finished = await repository.finish_claim(schedule_id, "lease-1", run_id, WorkflowRunStatus.COMPLETED)
    finish_call = collection.update_one.await_args

    assert disabled is True
    assert finished is True
    assert disable_call.args[0] == {"_id": schedule_id, "lease_token": "lease-1"}
    assert disable_call.args[1]["$set"]["enabled"] is False
    assert finish_call.args[0] == {"_id": schedule_id, "lease_token": "lease-1"}
    assert finish_call.args[1]["$set"]["last_run_id"] == run_id


@pytest.mark.asyncio
async def test_update_schedule_returns_validated_document(monkeypatch: pytest.MonkeyPatch) -> None:
    schedule_id = PydanticObjectId()
    workflow_id = PydanticObjectId()
    session = _Session()
    updated_schedule = object()
    collection = SimpleNamespace(find_one_and_update=AsyncMock(return_value={"_id": schedule_id}))
    repository = _repository(monkeypatch, collection)
    monkeypatch.setattr(
        schedule_repository.WorkflowSchedule,
        "model_validate",
        lambda _document: updated_schedule,
    )

    result = await repository.update_schedule(schedule_id, workflow_id, {"enabled": False}, session)

    assert result is updated_schedule
    query, update = collection.find_one_and_update.await_args.args
    assert query == {"_id": schedule_id, "workflow_definition_id": workflow_id}
    assert update == {"$set": {"enabled": False}}
    assert collection.find_one_and_update.await_args.kwargs["session"] is session
