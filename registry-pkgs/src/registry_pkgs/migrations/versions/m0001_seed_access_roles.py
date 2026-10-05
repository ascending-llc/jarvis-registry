"""Seed AccessRole documents for the Registry-owned ACL resource types.

Registry seeds federation, workflow, workflowSchedule, skill, skillSyncSource,
mcpServer, and remoteAgent roles.

Jarvis Chat predates Registry and owns seeding mcpServer roles in deployments
where it shares a MongoDB with Registry. This migration also seeds mcpServer here,
using the same accessRoleId/name/description/permBits values Chat already
creates there, so the upsert below is a no-op in a shared deployment — it only
fills the gap for a Registry-only deployment with no Chat present.

Idempotent: each role is upserted on its natural key `accessRoleId` with `$setOnInsert`
only, so a re-run (or a run after a partial one) inserts the missing roles and never
modifies an existing one.
"""

import logging
from datetime import UTC, datetime

from pymongo.asynchronous.database import AsyncDatabase

logger = logging.getLogger(__name__)


async def up(db: AsyncDatabase) -> None:
    now = datetime.now(UTC)
    # Chat owns its own resource types (agent, promptGroup) plus mcpServer (see module
    # docstring). Registry owns the rest, and also seeds mcpServer here so a Registry-only
    # deployment (no Chat) still gets it.
    roles_data = [
        {
            "accessRoleId": "federation_viewer",
            "resourceType": "federation",
            "name": "com_ui_federation_role_viewer",
            "description": "com_ui_federation_viewer_desc",
            "permBits": 1,
            "createdAt": now,
            "updatedAt": now,
            "__v": 0,
        },
        {
            "accessRoleId": "federation_editor",
            "resourceType": "federation",
            "name": "com_ui_federation_role_editor",
            "description": "com_ui_federation_editor_desc",
            "permBits": 3,
            "createdAt": now,
            "updatedAt": now,
            "__v": 0,
        },
        {
            "accessRoleId": "federation_owner",
            "resourceType": "federation",
            "name": "com_ui_federation_role_owner",
            "description": "com_ui_federation_owner_desc",
            "permBits": 15,
            "createdAt": now,
            "updatedAt": now,
            "__v": 0,
        },
        {
            "accessRoleId": "workflow_viewer",
            "resourceType": "workflow",
            "name": "com_ui_workflow_role_viewer",
            "description": "com_ui_workflow_viewer_desc",
            "permBits": 1,
            "createdAt": now,
            "updatedAt": now,
            "__v": 0,
        },
        {
            "accessRoleId": "workflow_editor",
            "resourceType": "workflow",
            "name": "com_ui_workflow_role_editor",
            "description": "com_ui_workflow_editor_desc",
            "permBits": 3,
            "createdAt": now,
            "updatedAt": now,
            "__v": 0,
        },
        {
            "accessRoleId": "workflow_owner",
            "resourceType": "workflow",
            "name": "com_ui_workflow_role_owner",
            "description": "com_ui_workflow_owner_desc",
            "permBits": 15,
            "createdAt": now,
            "updatedAt": now,
            "__v": 0,
        },
        {
            "accessRoleId": "skill_viewer",
            "resourceType": "skill",
            "name": "com_ui_skill_role_viewer",
            "description": "com_ui_skill_viewer_desc",
            "permBits": 1,
            "createdAt": now,
            "updatedAt": now,
            "__v": 0,
        },
        {
            "accessRoleId": "workflow_schedule_viewer",
            "resourceType": "workflowSchedule",
            "name": "com_ui_workflow_schedule_role_viewer",
            "description": "com_ui_workflow_schedule_viewer_desc",
            "permBits": 1,
            "createdAt": now,
            "updatedAt": now,
            "__v": 0,
        },
        {
            "accessRoleId": "workflow_schedule_editor",
            "resourceType": "workflowSchedule",
            "name": "com_ui_workflow_schedule_role_editor",
            "description": "com_ui_workflow_schedule_editor_desc",
            "permBits": 3,
            "createdAt": now,
            "updatedAt": now,
            "__v": 0,
        },
        {
            "accessRoleId": "workflow_schedule_owner",
            "resourceType": "workflowSchedule",
            "name": "com_ui_workflow_schedule_role_owner",
            "description": "com_ui_workflow_schedule_owner_desc",
            "permBits": 15,
            "createdAt": now,
            "updatedAt": now,
            "__v": 0,
        },
        {
            "accessRoleId": "skill_editor",
            "resourceType": "skill",
            "name": "com_ui_skill_role_editor",
            "description": "com_ui_skill_editor_desc",
            "permBits": 3,
            "createdAt": now,
            "updatedAt": now,
            "__v": 0,
        },
        {
            "accessRoleId": "skill_owner",
            "resourceType": "skill",
            "name": "com_ui_skill_role_owner",
            "description": "com_ui_skill_owner_desc",
            "permBits": 15,
            "createdAt": now,
            "updatedAt": now,
            "__v": 0,
        },
        {
            "accessRoleId": "skill_sync_source_viewer",
            "resourceType": "skillSyncSource",
            "name": "com_ui_skill_sync_source_role_viewer",
            "description": "com_ui_skill_sync_source_viewer_desc",
            "permBits": 1,
            "createdAt": now,
            "updatedAt": now,
            "__v": 0,
        },
        {
            "accessRoleId": "skill_sync_source_editor",
            "resourceType": "skillSyncSource",
            "name": "com_ui_skill_sync_source_role_editor",
            "description": "com_ui_skill_sync_source_editor_desc",
            "permBits": 3,
            "createdAt": now,
            "updatedAt": now,
            "__v": 0,
        },
        {
            "accessRoleId": "skill_sync_source_owner",
            "resourceType": "skillSyncSource",
            "name": "com_ui_skill_sync_source_role_owner",
            "description": "com_ui_skill_sync_source_owner_desc",
            "permBits": 15,
            "createdAt": now,
            "updatedAt": now,
            "__v": 0,
        },
        {
            "accessRoleId": "mcpServer_viewer",
            "resourceType": "mcpServer",
            "name": "com_ui_mcp_server_role_viewer",
            "description": "com_ui_mcp_server_role_viewer_desc",
            "permBits": 1,
            "createdAt": now,
            "updatedAt": now,
            "__v": 0,
        },
        {
            "accessRoleId": "mcpServer_editor",
            "resourceType": "mcpServer",
            "name": "com_ui_mcp_server_role_editor",
            "description": "com_ui_mcp_server_role_editor_desc",
            "permBits": 3,
            "createdAt": now,
            "updatedAt": now,
            "__v": 0,
        },
        {
            "accessRoleId": "mcpServer_owner",
            "resourceType": "mcpServer",
            "name": "com_ui_mcp_server_role_owner",
            "description": "com_ui_mcp_server_role_owner_desc",
            "permBits": 15,
            "createdAt": now,
            "updatedAt": now,
            "__v": 0,
        },
        {
            "accessRoleId": "remoteAgent_viewer",
            "resourceType": "remoteAgent",
            "name": "com_ui_remote_agent_role_viewer",
            "description": "com_ui_remote_agent_role_viewer_desc",
            "permBits": 1,
            "createdAt": now,
            "updatedAt": now,
            "__v": 0,
        },
        {
            "accessRoleId": "remoteAgent_editor",
            "resourceType": "remoteAgent",
            "name": "com_ui_remote_agent_role_editor",
            "description": "com_ui_remote_agent_role_editor_desc",
            "permBits": 3,
            "createdAt": now,
            "updatedAt": now,
            "__v": 0,
        },
        {
            "accessRoleId": "remoteAgent_owner",
            "resourceType": "remoteAgent",
            "name": "com_ui_remote_agent_role_owner",
            "description": "com_ui_remote_agent_role_owner_desc",
            "permBits": 15,
            "createdAt": now,
            "updatedAt": now,
            "__v": 0,
        },
    ]

    collection = db["accessroles"]
    inserted = 0
    for role_data in roles_data:
        result = await collection.update_one(
            {"accessRoleId": role_data["accessRoleId"]}, {"$setOnInsert": role_data}, upsert=True
        )
        if result.upserted_id is not None:
            inserted += 1
            logger.info("Inserted access role %s (permBits=%s)", role_data["accessRoleId"], role_data["permBits"])

    logger.info("Access roles: %d inserted, %d already present", inserted, len(roles_data) - inserted)
