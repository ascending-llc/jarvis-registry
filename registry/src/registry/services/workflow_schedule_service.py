from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from beanie import PydanticObjectId
from fastapi import HTTPException, status
from pymongo.asynchronous.client_session import AsyncClientSession

from registry.schemas.acl_schema import ResourcePermissions
from registry.schemas.errors import ErrorCode, create_error_detail
from registry.schemas.workflow_schedule_schemas import ScheduleCreateRequest, ScheduleUpdateRequest
from registry.services.access_control_service import ACLService
from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.models import PrincipalType, WorkflowDefinition, WorkflowSchedule
from registry_pkgs.models.enums import RoleBits
from registry_pkgs.models.extended_access_role import RegistryResourceType
from registry_pkgs.workflows.schedule_repository import WorkflowScheduleRepository
from registry_pkgs.workflows.scheduling import calculate_next_run_at, validate_schedule


def _http_error(status_code: int, error_code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=create_error_detail(error_code, message),
    )


def _object_id(value: str, resource_name: str) -> PydanticObjectId:
    try:
        return PydanticObjectId(value)
    except Exception as exc:
        raise _http_error(
            status.HTTP_404_NOT_FOUND,
            ErrorCode.RESOURCE_NOT_FOUND,
            f"{resource_name} not found",
        ) from exc


@dataclass(frozen=True)
class ScheduleAccess:
    schedule: WorkflowSchedule
    permissions: ResourcePermissions


class WorkflowScheduleService:
    """Manage schedule documents while preserving workflow and schedule ACL boundaries."""

    def __init__(self, acl_service: ACLService, schedule_repository: WorkflowScheduleRepository) -> None:
        self._acl_service = acl_service
        self._schedule_repository = schedule_repository

    async def create_schedule(
        self,
        workflow_id: str,
        data: ScheduleCreateRequest,
        user_id: str,
    ) -> ScheduleAccess:
        workflow_oid = _object_id(workflow_id, "Workflow")
        user_oid = _object_id(user_id, "User")
        self._validate(data.cron_expression, data.timezone)
        now = datetime.now(UTC)
        schedule = WorkflowSchedule(
            workflow_definition_id=workflow_oid,
            cron_expression=data.cron_expression,
            timezone=data.timezone,
            initial_input=data.initial_input,
            enabled=False,
            created_by=user_oid,
            created_at=now,
            updated_at=now,
        )
        async with MongoDB.get_client().start_session() as mongo_session:
            async with await mongo_session.start_transaction():
                workflow = await self._require_workflow_permission(
                    workflow_oid,
                    user_oid,
                    "EDIT",
                    session=mongo_session,
                )
                if not workflow.enabled:
                    raise _http_error(
                        status.HTTP_409_CONFLICT,
                        ErrorCode.CONFLICT,
                        "Workflow must be enabled before scheduling",
                    )
                await schedule.insert(session=mongo_session)
                await self._acl_service.grant_permission(
                    principal_type=PrincipalType.USER,
                    principal_id=user_oid,
                    resource_type=RegistryResourceType.WORKFLOW_SCHEDULE.value,
                    resource_id=schedule.id,
                    perm_bits=RoleBits.OWNER,
                    session=mongo_session,
                )
        return ScheduleAccess(
            schedule=schedule,
            permissions=ResourcePermissions(VIEW=True, EDIT=True, DELETE=True, SHARE=True),
        )

    async def list_schedules(self, workflow_id: str, user_id: str) -> list[ScheduleAccess]:
        workflow_oid = _object_id(workflow_id, "Workflow")
        user_oid = _object_id(user_id, "User")
        await self._require_workflow_permission(workflow_oid, user_oid, "VIEW")
        accessible_ids = await self._acl_service.get_accessible_resource_ids(
            user_id=user_oid,
            resource_type=RegistryResourceType.WORKFLOW_SCHEDULE.value,
        )
        schedule_ids = [_object_id(resource_id, "Schedule") for resource_id in accessible_ids]
        if not schedule_ids:
            return []
        schedules = (
            await WorkflowSchedule.find(
                {
                    "_id": {"$in": schedule_ids},
                    "workflow_definition_id": workflow_oid,
                }
            )
            .sort("-created_at")
            .to_list()
        )
        permissions_by_id = await self._acl_service.get_user_permissions_for_resources(
            user_id=user_oid,
            resource_type=RegistryResourceType.WORKFLOW_SCHEDULE.value,
            resource_ids=[schedule.id for schedule in schedules],
        )
        return [
            ScheduleAccess(schedule=schedule, permissions=permissions_by_id[schedule.id])
            for schedule in schedules
            if permissions_by_id[schedule.id].VIEW
        ]

    async def get_schedule(self, workflow_id: str, schedule_id: str, user_id: str) -> ScheduleAccess:
        return await self._load_authorized_schedule(
            workflow_id,
            schedule_id,
            user_id,
            workflow_permission="VIEW",
            schedule_permission="VIEW",
        )

    async def update_schedule(
        self,
        workflow_id: str,
        schedule_id: str,
        data: ScheduleUpdateRequest,
        user_id: str,
    ) -> ScheduleAccess:
        async with MongoDB.get_client().start_session() as mongo_session:
            async with await mongo_session.start_transaction():
                access = await self._load_authorized_schedule(
                    workflow_id,
                    schedule_id,
                    user_id,
                    workflow_permission="EDIT",
                    schedule_permission="EDIT",
                    session=mongo_session,
                )
                updates = self._validated_updates(access.schedule, data)
                schedule = await self._schedule_repository.update_schedule(
                    access.schedule.id,
                    access.schedule.workflow_definition_id,
                    updates,
                    mongo_session,
                )
        if schedule is None:
            raise _http_error(status.HTTP_404_NOT_FOUND, ErrorCode.RESOURCE_NOT_FOUND, "Schedule not found")
        return ScheduleAccess(
            schedule=schedule,
            permissions=access.permissions,
        )

    async def toggle_schedule(
        self,
        workflow_id: str,
        schedule_id: str,
        enabled: bool,
        user_id: str,
    ) -> ScheduleAccess:
        async with MongoDB.get_client().start_session() as mongo_session:
            async with await mongo_session.start_transaction():
                access = await self._load_authorized_schedule(
                    workflow_id,
                    schedule_id,
                    user_id,
                    workflow_permission="EDIT",
                    schedule_permission="EDIT",
                    session=mongo_session,
                )
                updates = await self._toggle_updates(access.schedule, enabled, mongo_session)
                schedule = await self._schedule_repository.update_schedule(
                    access.schedule.id,
                    access.schedule.workflow_definition_id,
                    updates,
                    mongo_session,
                )
        if schedule is None:
            raise _http_error(status.HTTP_404_NOT_FOUND, ErrorCode.RESOURCE_NOT_FOUND, "Schedule not found")
        return ScheduleAccess(
            schedule=schedule,
            permissions=access.permissions,
        )

    async def delete_schedule(self, workflow_id: str, schedule_id: str, user_id: str) -> None:
        async with MongoDB.get_client().start_session() as mongo_session:
            async with await mongo_session.start_transaction():
                access = await self._load_authorized_schedule(
                    workflow_id,
                    schedule_id,
                    user_id,
                    workflow_permission="EDIT",
                    schedule_permission="DELETE",
                    session=mongo_session,
                )
                await access.schedule.delete(session=mongo_session)
                deleted_acl_count = await self._acl_service.delete_acl_entries_for_resource(
                    resource_type=RegistryResourceType.WORKFLOW_SCHEDULE.value,
                    resource_id=access.schedule.id,
                    session=mongo_session,
                )
                if deleted_acl_count == 0:
                    raise RuntimeError("Schedule ACL cleanup failed")

    async def _load_schedule(
        self,
        workflow_id: PydanticObjectId,
        schedule_id: PydanticObjectId,
        session: AsyncClientSession | None = None,
    ) -> WorkflowSchedule:
        schedule = await WorkflowSchedule.find_one(
            WorkflowSchedule.id == schedule_id,
            WorkflowSchedule.workflow_definition_id == workflow_id,
            session=session,
        )
        if schedule is None:
            raise _http_error(status.HTTP_404_NOT_FOUND, ErrorCode.RESOURCE_NOT_FOUND, "Schedule not found")
        return schedule

    async def _load_authorized_schedule(
        self,
        workflow_id: str,
        schedule_id: str,
        user_id: str,
        workflow_permission: str,
        schedule_permission: str,
        session: AsyncClientSession | None = None,
    ) -> ScheduleAccess:
        workflow_oid = _object_id(workflow_id, "Workflow")
        schedule_oid = _object_id(schedule_id, "Schedule")
        user_oid = _object_id(user_id, "User")
        await self._require_workflow_permission(workflow_oid, user_oid, workflow_permission, session=session)
        schedule = await self._load_schedule(workflow_oid, schedule_oid, session=session)
        permissions = await self._acl_service.check_user_permission(
            user_id=user_oid,
            resource_type=RegistryResourceType.WORKFLOW_SCHEDULE.value,
            resource_id=schedule.id,
            required_permission=schedule_permission,
            session=session,
        )
        return ScheduleAccess(schedule=schedule, permissions=permissions)

    async def _require_workflow_permission(
        self,
        workflow_id: PydanticObjectId,
        user_id: PydanticObjectId,
        permission: str,
        session: AsyncClientSession | None = None,
    ) -> WorkflowDefinition:
        workflow = await WorkflowDefinition.get(workflow_id, session=session)
        if workflow is None:
            raise _http_error(status.HTTP_404_NOT_FOUND, ErrorCode.RESOURCE_NOT_FOUND, "Workflow not found")
        await self._acl_service.check_user_permission(
            user_id=user_id,
            resource_type=RegistryResourceType.WORKFLOW.value,
            resource_id=workflow_id,
            required_permission=permission,
            session=session,
        )
        return workflow

    def _validated_updates(
        self,
        schedule: WorkflowSchedule,
        data: ScheduleUpdateRequest,
    ) -> dict[str, Any]:
        updates: dict[str, Any] = data.model_dump(exclude_unset=True)
        cron_expression = updates.get("cron_expression", schedule.cron_expression)
        timezone_name = updates.get("timezone", schedule.timezone)
        if cron_expression is None:
            raise _http_error(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                ErrorCode.INVALID_PARAMETER,
                "cron_expression cannot be null",
            )
        if timezone_name is None:
            raise _http_error(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                ErrorCode.INVALID_PARAMETER,
                "timezone cannot be null",
            )
        self._validate(cron_expression, timezone_name)
        if schedule.enabled and {"cron_expression", "timezone"}.intersection(updates):
            updates["next_run_at"] = calculate_next_run_at(cron_expression, timezone_name)
        updates["updated_at"] = datetime.now(UTC)
        return updates

    async def _toggle_updates(
        self,
        schedule: WorkflowSchedule,
        enabled: bool,
        session: AsyncClientSession,
    ) -> dict[str, Any]:
        if enabled:
            workflow = await WorkflowDefinition.get(schedule.workflow_definition_id, session=session)
            if workflow is None or not workflow.enabled:
                raise _http_error(
                    status.HTTP_409_CONFLICT,
                    ErrorCode.CONFLICT,
                    "Workflow must be enabled before scheduling",
                )
            next_run_at = calculate_next_run_at(schedule.cron_expression, schedule.timezone)
        else:
            next_run_at = None
        return {
            "enabled": enabled,
            "next_run_at": next_run_at,
            "locked_until": None,
            "lease_token": None,
            "updated_at": datetime.now(UTC),
        }

    @staticmethod
    def _validate(cron_expression: str, timezone_name: str) -> None:
        try:
            validate_schedule(cron_expression, timezone_name)
        except ValueError as exc:
            raise _http_error(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                ErrorCode.INVALID_PARAMETER,
                str(exc),
            ) from exc
