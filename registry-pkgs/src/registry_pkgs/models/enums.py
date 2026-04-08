from enum import StrEnum


class ToolDiscoveryMode(StrEnum):
    """Tool discovery mode enumeration"""

    EXTERNAL = "external"
    EMBEDDED = "embedded"


class ServerEntityType(StrEnum):
    """Entity type enumeration for vector documents"""

    SERVER = "server"
    TOOL = "tool"
    RESOURCE = "resource"
    PROMPT = "prompt"


class PermissionBits:
    VIEW = 1  # 0001
    EDIT = 2  # 0010
    DELETE = 4  # 0100
    SHARE = 8  # 1000


class RoleBits:
    VIEWER = PermissionBits.VIEW  # 1
    EDITOR = PermissionBits.VIEW | PermissionBits.EDIT  # 3
    OWNER = PermissionBits.VIEW | PermissionBits.EDIT | PermissionBits.DELETE | PermissionBits.SHARE  # 15


class OAuthProviderType(StrEnum):
    COGNITO = "cognito"
    AUTH0 = "auth0"
    OKTA = "okta"
    ENTRA_ID = "entra_id"
    CUSTOM_OAUTH2 = "custom"


class FederationProviderType(StrEnum):
    """Supported external federation provider types."""

    AWS_AGENTCORE = "aws_agentcore"
    AZURE_AI_FOUNDRY = "azure_ai_foundry"


class FederationStatus(StrEnum):
    """Lifecycle status for a federation definition."""

    ACTIVE = "active"  # Available for normal use
    DELETING = "deleting"  # Delete workflow is in progress
    DELETED = "deleted"  # Soft-deleted and no longer available

    def is_active(self) -> bool:
        return self == FederationStatus.ACTIVE

    def is_deleted(self) -> bool:
        return self == FederationStatus.DELETED


class FederationSyncStatus(StrEnum):
    """Sync execution status used by the federation state machine."""

    IDLE = "idle"  # Never synced yet
    PENDING = "pending"  # Sync job created and waiting to run
    SYNCING = "syncing"  # Sync is currently running
    SUCCESS = "success"  # Last sync completed successfully
    FAILED = "failed"  # Last sync failed

    def is_running(self) -> bool:
        """Return True when a sync is queued or actively running."""
        return self in {
            FederationSyncStatus.PENDING,
            FederationSyncStatus.SYNCING,
        }

    def is_terminal(self) -> bool:
        """Return True when the current sync state is terminal."""
        return self in {
            FederationSyncStatus.SUCCESS,
            FederationSyncStatus.FAILED,
        }

    def is_success(self) -> bool:
        """Return True when the last sync completed successfully."""
        return self == FederationSyncStatus.SUCCESS

    def is_failed(self) -> bool:
        """Return True when the last sync ended in failure."""
        return self == FederationSyncStatus.FAILED


class FederationJobType(StrEnum):
    """Types of federation sync jobs."""

    FULL_SYNC = "full_sync"  # Regular full sync
    CONFIG_RESYNC = "config_resync"  # Triggered by config change
    DELETE_SYNC = "delete_sync"  # Cleanup during delete


class FederationJobStatus(StrEnum):
    """Execution status for a federation sync job."""

    PENDING = "pending"
    SYNCING = "syncing"
    SUCCESS = "success"
    FAILED = "failed"

    def is_running(self) -> bool:
        """Return True when the job has not yet reached a terminal state."""
        return self in {
            FederationJobStatus.PENDING,
            FederationJobStatus.SYNCING,
        }

    def is_terminal(self) -> bool:
        """Return True when the job finished with any terminal outcome."""
        return self in {
            FederationJobStatus.SUCCESS,
            FederationJobStatus.FAILED,
        }


class FederationJobPhase(StrEnum):
    """Execution phase for logging, debugging, and UI display."""

    QUEUED = "queued"
    DISCOVERING = "discovering"
    APPLYING = "applying"
    COMPLETED = "completed"
    FAILED = "failed"


class FederationTriggerType(StrEnum):
    """Source that triggered a federation sync job."""

    MANUAL = "manual"  # Triggered manually from the UI
    API = "api"  # Triggered by an API workflow


class FederationStateMachine:
    """Centralized rules for federation state validation and transitions."""

    @staticmethod
    def can_start_sync(sync_status: FederationSyncStatus) -> bool:
        """Return True when a new sync may start from the current sync status."""
        return sync_status in {
            FederationSyncStatus.IDLE,
            FederationSyncStatus.SUCCESS,
            FederationSyncStatus.FAILED,
        }

    @staticmethod
    def can_delete(status: FederationStatus) -> bool:
        """Return True when the federation is allowed to enter the delete flow."""
        return status == FederationStatus.ACTIVE

    @staticmethod
    def can_update(status: FederationStatus) -> bool:
        """Return True when federation metadata/config may be updated."""
        return status == FederationStatus.ACTIVE

    @staticmethod
    def transition_to_sync_pending(
        status: FederationStatus,
        sync_status: FederationSyncStatus,
    ) -> FederationSyncStatus:
        """Validate and return the sync status for a newly queued sync job."""
        if status != FederationStatus.ACTIVE:
            raise ValueError(f"Federation in status '{status}' cannot transition to sync pending")
        if not FederationStateMachine.can_start_sync(sync_status):
            raise ValueError(f"Federation in sync status '{sync_status}' cannot transition to sync pending")
        return FederationSyncStatus.PENDING

    @staticmethod
    def transition_to_syncing(
        status: FederationStatus,
        sync_status: FederationSyncStatus,
    ) -> FederationSyncStatus:
        """Validate and return the sync status for an actively running sync."""
        if status != FederationStatus.ACTIVE:
            raise ValueError(f"Federation in status '{status}' cannot transition to syncing")
        if sync_status not in {FederationSyncStatus.PENDING, FederationSyncStatus.SYNCING}:
            raise ValueError(f"Federation in sync status '{sync_status}' cannot transition to syncing")
        return FederationSyncStatus.SYNCING

    @staticmethod
    def transition_to_sync_success(
        sync_status: FederationSyncStatus,
    ) -> FederationSyncStatus:
        """Validate and return the sync status for a successful completion."""
        if sync_status not in {
            FederationSyncStatus.PENDING,
            FederationSyncStatus.SYNCING,
            FederationSyncStatus.SUCCESS,
        }:
            raise ValueError(f"Federation in sync status '{sync_status}' cannot transition to success")
        return FederationSyncStatus.SUCCESS

    @staticmethod
    def transition_to_sync_failed(
        sync_status: FederationSyncStatus,
    ) -> FederationSyncStatus:
        """Validate and return the sync status for a failed completion."""
        if sync_status not in {
            FederationSyncStatus.PENDING,
            FederationSyncStatus.SYNCING,
            FederationSyncStatus.FAILED,
        }:
            raise ValueError(f"Federation in sync status '{sync_status}' cannot transition to failed")
        return FederationSyncStatus.FAILED

    @staticmethod
    def transition_to_deleting(status: FederationStatus) -> FederationStatus:
        """Validate and return the lifecycle status for delete-in-progress."""
        if not FederationStateMachine.can_delete(status):
            raise ValueError(f"Federation in status '{status}' cannot transition to deleting")
        return FederationStatus.DELETING

    @staticmethod
    def transition_to_deleted(status: FederationStatus) -> FederationStatus:
        """Validate and return the lifecycle status for a completed delete."""
        if status not in {FederationStatus.ACTIVE, FederationStatus.DELETING}:
            raise ValueError(f"Federation in status '{status}' cannot transition to deleted")
        return FederationStatus.DELETED

    @staticmethod
    def transition_to_delete_failed(status: FederationStatus) -> FederationStatus:
        """Return the lifecycle status after a delete workflow fails and recovery is needed."""
        if status != FederationStatus.DELETING:
            raise ValueError(f"Federation in status '{status}' cannot transition to delete failed")
        return FederationStatus.ACTIVE


class FederationJobStateMachine:
    """Centralized rules for federation job status transitions."""

    @staticmethod
    def transition_to_syncing(status: FederationJobStatus) -> FederationJobStatus:
        if status not in {FederationJobStatus.PENDING, FederationJobStatus.SYNCING}:
            raise ValueError(f"Federation job in status '{status}' cannot transition to syncing")
        return FederationJobStatus.SYNCING

    @staticmethod
    def transition_to_success(status: FederationJobStatus) -> FederationJobStatus:
        if status not in {
            FederationJobStatus.PENDING,
            FederationJobStatus.SYNCING,
            FederationJobStatus.SUCCESS,
        }:
            raise ValueError(f"Federation job in status '{status}' cannot transition to success")
        return FederationJobStatus.SUCCESS

    @staticmethod
    def transition_to_failed(status: FederationJobStatus) -> FederationJobStatus:
        if status not in {
            FederationJobStatus.PENDING,
            FederationJobStatus.SYNCING,
            FederationJobStatus.FAILED,
        }:
            raise ValueError(f"Federation job in status '{status}' cannot transition to failed")
        return FederationJobStatus.FAILED
