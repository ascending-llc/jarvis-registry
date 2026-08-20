"""Business logic and ACL enforcement for workflow schedules."""

from datetime import UTC, datetime
from typing import Any

from beanie import PydanticObjectId
from fastapi import HTTPException, status

from registry.schemas.workflow_schedule_schemas import ScheduleCreateRequest, ScheduleUpdateRequest
from registry.services.access_control_service import ACLService
from registry_pkgs.database.mongodb import MongoDB
from registry_pkgs.models import PrincipalType, WorkflowDefinition, WorkflowSchedule
from registry_pkgs.models.enums import RoleBits
from registry_pkgs.models.extended_access_role import RegistryResourceType
from registry_pkgs.workflows.scheduling import calculate_next_run_at, validate_schedule


def _object_id(value: str, resource_name: str) -> PydanticObjectId:
    try:
        return PydanticObjectId(value)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{resource_name} not found") from exc


class WorkflowScheduleService:
    """Manage schedule documents while preserving workflow and schedule ACL boundaries."""

    def __init__(self, acl_service: ACLService) -> None:
        self._acl_service = acl_service

    async def create_schedule(
        self,
        workflow_id: str,
        data: ScheduleCreateRequest,
        user_id: str,
    ) -> WorkflowSchedule:
        workflow_oid = _object_id(workflow_id, "Workflow")
        user_oid = _object_id(user_id, "User")
        workflow = await WorkflowDefinition.get(workflow_oid)
        if workflow is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")
        await self._acl_service.check_user_permission(
            user_id=user_oid,
            resource_type=RegistryResourceType.WORKFLOW.value,
            resource_id=workflow_oid,
            required_permission="EDIT",
        )
        if not workflow.enabled:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Workflow must be enabled before scheduling"
            )
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
                await schedule.insert(session=mongo_session)
                await self._acl_service.grant_permission(
                    principal_type=PrincipalType.USER,
                    principal_id=user_oid,
                    resource_type=RegistryResourceType.WORKFLOW_SCHEDULE.value,
                    resource_id=schedule.id,
                    perm_bits=RoleBits.OWNER,
                    session=mongo_session,
                )
        return schedule

    async def list_schedules(self, workflow_id: str, user_id: str) -> list[WorkflowSchedule]:
        workflow_oid = _object_id(workflow_id, "Workflow")
        await self._require_workflow_permission(workflow_oid, user_id, "VIEW")
        return (
            await WorkflowSchedule.find(WorkflowSchedule.workflow_definition_id == workflow_oid)
            .sort("-created_at")
            .to_list()
        )

    async def get_schedule(self, workflow_id: str, schedule_id: str, user_id: str) -> WorkflowSchedule:
        schedule = await self._load_schedule(workflow_id, schedule_id)
        await self._require_schedule_permission(schedule.id, user_id, "VIEW")
        return schedule

    async def update_schedule(
        self,
        workflow_id: str,
        schedule_id: str,
        data: ScheduleUpdateRequest,
        user_id: str,
    ) -> WorkflowSchedule:
        schedule = await self._load_schedule(workflow_id, schedule_id)
        await self._require_schedule_permission(schedule.id, user_id, "EDIT")
        updates: dict[str, Any] = data.model_dump(exclude_unset=True)
        if updates.get("cron_expression", schedule.cron_expression) is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="cron_expression cannot be null",
            )
        if updates.get("timezone", schedule.timezone) is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="timezone cannot be null",
            )
        cron_expression = updates.get("cron_expression", schedule.cron_expression)
        timezone_name = updates.get("timezone", schedule.timezone)
        self._validate(cron_expression, timezone_name)
        for field_name, value in updates.items():
            setattr(schedule, field_name, value)
        if schedule.enabled and {"cron_expression", "timezone"}.intersection(updates):
            schedule.next_run_at = calculate_next_run_at(cron_expression, timezone_name)
        schedule.updated_at = datetime.now(UTC)
        await schedule.save()
        return schedule

    async def toggle_schedule(
        self,
        workflow_id: str,
        schedule_id: str,
        enabled: bool,
        user_id: str,
    ) -> WorkflowSchedule:
        schedule = await self._load_schedule(workflow_id, schedule_id)
        await self._require_schedule_permission(schedule.id, user_id, "EDIT")
        if enabled:
            workflow = await WorkflowDefinition.get(schedule.workflow_definition_id)
            if workflow is None or not workflow.enabled:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT, detail="Workflow must be enabled before scheduling"
                )
            schedule.next_run_at = calculate_next_run_at(schedule.cron_expression, schedule.timezone)
        else:
            schedule.next_run_at = None
            schedule.locked_until = None
        schedule.enabled = enabled
        schedule.updated_at = datetime.now(UTC)
        await schedule.save()
        return schedule

    async def delete_schedule(self, workflow_id: str, schedule_id: str, user_id: str) -> None:
        schedule = await self._load_schedule(workflow_id, schedule_id)
        await self._require_schedule_permission(schedule.id, user_id, "DELETE")
        async with MongoDB.get_client().start_session() as mongo_session:
            async with await mongo_session.start_transaction():
                await schedule.delete(session=mongo_session)
                await self._acl_service.delete_acl_entries_for_resource(
                    resource_type=RegistryResourceType.WORKFLOW_SCHEDULE.value,
                    resource_id=schedule.id,
                    session=mongo_session,
                )

    async def _load_schedule(self, workflow_id: str, schedule_id: str) -> WorkflowSchedule:
        workflow_oid = _object_id(workflow_id, "Workflow")
        schedule_oid = _object_id(schedule_id, "Schedule")
        schedule = await WorkflowSchedule.find_one(
            WorkflowSchedule.id == schedule_oid,
            WorkflowSchedule.workflow_definition_id == workflow_oid,
        )
        if schedule is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Schedule not found")
        return schedule

    async def _require_workflow_permission(
        self,
        workflow_id: PydanticObjectId,
        user_id: str,
        permission: str,
    ) -> None:
        if await WorkflowDefinition.get(workflow_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow not found")
        await self._acl_service.check_user_permission(
            user_id=_object_id(user_id, "User"),
            resource_type=RegistryResourceType.WORKFLOW.value,
            resource_id=workflow_id,
            required_permission=permission,
        )

    async def _require_schedule_permission(
        self,
        schedule_id: PydanticObjectId,
        user_id: str,
        permission: str,
    ) -> None:
        await self._acl_service.check_user_permission(
            user_id=_object_id(user_id, "User"),
            resource_type=RegistryResourceType.WORKFLOW_SCHEDULE.value,
            resource_id=schedule_id,
            required_permission=permission,
        )

    @staticmethod
    def _validate(cron_expression: str, timezone_name: str) -> None:
        try:
            validate_schedule(cron_expression, timezone_name)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
