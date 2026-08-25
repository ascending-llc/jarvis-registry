from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from beanie import PydanticObjectId
from pymongo import ReturnDocument
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase

from registry_pkgs.models import WorkflowRun, WorkflowSchedule
from registry_pkgs.models.enums import WorkflowRunStatus


def _lease_filter(schedule_id: PydanticObjectId, lease_token: str) -> dict[str, Any]:
    return {"_id": schedule_id, "lease_token": lease_token}


def _claimable_filter(now: datetime, due_before: datetime | None = None) -> dict[str, Any]:
    query: dict[str, Any] = {
        "enabled": True,
        "next_run_at": {"$ne": None},
        "$or": [
            {"locked_until": None},
            {"locked_until": {"$exists": False}},
            {"locked_until": {"$lt": now}},
        ],
    }
    if due_before is not None:
        query["next_run_at"] = {"$lte": due_before}
    return query


class WorkflowScheduleRepository:
    """Own raw MongoDB queries and fenced writes for ``WorkflowSchedule``."""

    def __init__(self, database: AsyncDatabase[dict[str, Any]]) -> None:
        self._database = database

    @property
    def _collection(self) -> AsyncCollection[dict[str, Any]]:
        return self._database.get_collection(WorkflowSchedule.get_settings().name)

    async def claim_due(self, lease_seconds: int) -> WorkflowSchedule | None:
        now = datetime.now(UTC)
        document = await self._collection.find_one_and_update(
            _claimable_filter(now, due_before=now),
            {
                "$set": {
                    "locked_until": now + timedelta(seconds=lease_seconds),
                    "lease_token": str(uuid4()),
                }
            },
            sort=[("next_run_at", 1)],
            return_document=ReturnDocument.AFTER,
        )
        return WorkflowSchedule.model_validate(document) if document is not None else None

    async def peek_next_deadline(self) -> datetime | None:
        document = await self._collection.find_one(
            _claimable_filter(datetime.now(UTC)),
            projection={"next_run_at": 1},
            sort=[("next_run_at", 1)],
        )
        return document["next_run_at"] if document is not None else None

    async def renew_claim(
        self,
        schedule_id: PydanticObjectId,
        lease_token: str,
        lease_seconds: int,
    ) -> bool:
        result = await self._collection.update_one(
            _lease_filter(schedule_id, lease_token),
            {"$set": {"locked_until": datetime.now(UTC) + timedelta(seconds=lease_seconds)}},
        )
        return result.matched_count > 0

    async def load_claim(
        self,
        schedule_id: PydanticObjectId,
        lease_token: str,
    ) -> WorkflowSchedule | None:
        document = await self._collection.find_one(
            {
                **_lease_filter(schedule_id, lease_token),
                "enabled": True,
            }
        )
        return WorkflowSchedule.model_validate(document) if document is not None else None

    async def advance_and_insert_run(
        self,
        claimed: WorkflowSchedule,
        run: WorkflowRun,
        next_run_at: datetime,
    ) -> bool:
        async with self._database.client.start_session() as mongo_session:
            async with await mongo_session.start_transaction():
                result = await self._collection.update_one(
                    {
                        **_lease_filter(claimed.id, claimed.lease_token),
                        "enabled": True,
                        "cron_expression": claimed.cron_expression,
                        "timezone": claimed.timezone,
                    },
                    {"$set": {"next_run_at": next_run_at}},
                    session=mongo_session,
                )
                if result.matched_count == 0:
                    return False
                await run.insert(session=mongo_session)
        return True

    async def disable_claim(self, schedule_id: PydanticObjectId, lease_token: str) -> bool:
        result = await self._collection.update_one(
            _lease_filter(schedule_id, lease_token),
            {
                "$set": {
                    "enabled": False,
                    "next_run_at": None,
                    "locked_until": None,
                    "lease_token": None,
                }
            },
        )
        return result.matched_count > 0

    async def finish_claim(
        self,
        schedule_id: PydanticObjectId,
        lease_token: str,
        run_id: PydanticObjectId | None,
        final_status: WorkflowRunStatus,
    ) -> bool:
        result = await self._collection.update_one(
            _lease_filter(schedule_id, lease_token),
            {
                "$set": {
                    "last_run_at": datetime.now(UTC),
                    "last_run_id": run_id,
                    "last_run_status": final_status,
                    "locked_until": None,
                    "lease_token": None,
                }
            },
        )
        return result.matched_count > 0

    async def update_schedule(
        self,
        schedule_id: PydanticObjectId,
        workflow_definition_id: PydanticObjectId,
        updates: dict[str, Any],
        session: AsyncClientSession,
    ) -> WorkflowSchedule | None:
        document = await self._collection.find_one_and_update(
            {
                "_id": schedule_id,
                "workflow_definition_id": workflow_definition_id,
            },
            {"$set": updates},
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        return WorkflowSchedule.model_validate(document) if document is not None else None
