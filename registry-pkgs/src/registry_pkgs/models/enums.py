from enum import StrEnum


class ToolDiscoveryMode(StrEnum):
    """Tool discovery mode enumeration"""

    EXTERNAL = "external"
    EMBEDDED = "embedded"


class MCPEntityType(StrEnum):
    """Entity types stored in the MCP_Servers Weaviate collection."""

    TOOL = "tool"
    RESOURCE = "resource"
    PROMPT = "prompt"


class A2AEntityType(StrEnum):
    """Entity types stored in the A2a_agents Weaviate collection."""

    AGENT = "agent"
    SKILL = "skill"


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


class McpAuthMode(StrEnum):
    """How an ExtendedMCPServer authenticates to its downstream MCP server."""

    AGENTCORE = "agentcore"
    OAUTH = "oauth"
    API_KEY = "api_key"
    NONE = "none"


class SkillSyncProviderType(StrEnum):
    """Supported providers for external skill sources."""

    GITHUB = "github"


class SkillSyncSourceStatus(StrEnum):
    ACTIVE = "active"
    DELETING = "deleting"
    DELETED = "deleted"


class SkillSyncStatus(StrEnum):
    IDLE = "idle"
    PENDING = "pending"
    SYNCING = "syncing"
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"

    def is_running(self) -> bool:
        return self in {SkillSyncStatus.PENDING, SkillSyncStatus.SYNCING}


class SkillSyncJobType(StrEnum):
    FULL_SYNC = "full_sync"
    CONFIG_RESYNC = "config_resync"
    DELETE_SYNC = "delete_sync"


class SkillSyncTriggerType(StrEnum):
    MANUAL = "manual"
    OAUTH_CALLBACK = "oauth_callback"
    API = "api"


class SkillSyncJobStatus(StrEnum):
    PENDING = "pending"
    SYNCING = "syncing"
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"


class SkillSyncJobPhase(StrEnum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    EXTRACTING = "extracting"
    DISCOVERING = "discovering"
    APPLYING = "applying"
    COMPLETED = "completed"
    FAILED = "failed"


class SkillSyncJobErrorCode(StrEnum):
    GITHUB_AUTH_FAILED = "github_auth_failed"
    GITHUB_RATE_LIMITED = "github_rate_limited"
    GITHUB_NOT_FOUND = "github_not_found"
    DOWNLOAD_FAILED = "download_failed"
    DOWNLOAD_TOO_LARGE = "download_too_large"
    EXTRACTION_FAILED = "extraction_failed"
    DECOMPRESSION_BOMB = "decompression_bomb"
    NO_SKILLS_FOUND = "no_skills_found"
    SYNC_NOT_IMPLEMENTED = "sync_not_implemented"
    INTERNAL_ERROR = "internal_error"


class SkillSyncSkillErrorCode(StrEnum):
    SKILL_PARSE_FAILED = "skill_parse_failed"
    SKILL_NAME_MISSING = "skill_name_missing"
    DUPLICATE_SKILL_NAME = "duplicate_skill_name"
    FILE_TOO_LARGE = "file_too_large"
    TOO_MANY_FILES = "too_many_files"
    WRITE_FAILED = "write_failed"
    DELETE_FAILED = "delete_failed"


class SkillSyncStateMachine:
    """State transition guards for skill sync sources and jobs."""

    @staticmethod
    def can_start_sync(sync_status: SkillSyncStatus) -> bool:
        return sync_status in {
            SkillSyncStatus.IDLE,
            SkillSyncStatus.SUCCESS,
            SkillSyncStatus.PARTIAL_SUCCESS,
            SkillSyncStatus.FAILED,
        }

    @staticmethod
    def can_update(status: SkillSyncSourceStatus) -> bool:
        return status == SkillSyncSourceStatus.ACTIVE

    @staticmethod
    def can_delete(status: SkillSyncSourceStatus) -> bool:
        return status == SkillSyncSourceStatus.ACTIVE

    @staticmethod
    def transition_to_sync_pending(
        status: SkillSyncSourceStatus,
        sync_status: SkillSyncStatus,
    ) -> SkillSyncStatus:
        if status != SkillSyncSourceStatus.ACTIVE:
            raise ValueError(f"Skill sync source in status '{status}' cannot start a sync")
        if not SkillSyncStateMachine.can_start_sync(sync_status):
            raise ValueError("Skill sync source already has an active sync job")
        return SkillSyncStatus.PENDING

    @staticmethod
    def transition_to_syncing(
        status: SkillSyncSourceStatus,
        sync_status: SkillSyncStatus,
    ) -> SkillSyncStatus:
        if status != SkillSyncSourceStatus.ACTIVE:
            raise ValueError(f"Skill sync source in status '{status}' cannot transition to syncing")
        if sync_status not in {SkillSyncStatus.PENDING, SkillSyncStatus.SYNCING}:
            raise ValueError(f"Skill sync source in sync status '{sync_status}' cannot transition to syncing")
        return SkillSyncStatus.SYNCING

    @staticmethod
    def transition_to_sync_success(sync_status: SkillSyncStatus) -> SkillSyncStatus:
        if sync_status not in {SkillSyncStatus.PENDING, SkillSyncStatus.SYNCING, SkillSyncStatus.SUCCESS}:
            raise ValueError(f"Skill sync source in sync status '{sync_status}' cannot transition to success")
        return SkillSyncStatus.SUCCESS

    @staticmethod
    def transition_to_sync_partial_success(sync_status: SkillSyncStatus) -> SkillSyncStatus:
        if sync_status not in {SkillSyncStatus.PENDING, SkillSyncStatus.SYNCING, SkillSyncStatus.PARTIAL_SUCCESS}:
            raise ValueError(f"Skill sync source in sync status '{sync_status}' cannot transition to partial_success")
        return SkillSyncStatus.PARTIAL_SUCCESS

    @staticmethod
    def transition_to_sync_failed(sync_status: SkillSyncStatus) -> SkillSyncStatus:
        if sync_status not in {SkillSyncStatus.PENDING, SkillSyncStatus.SYNCING, SkillSyncStatus.FAILED}:
            raise ValueError(f"Skill sync source in sync status '{sync_status}' cannot transition to failed")
        return SkillSyncStatus.FAILED

    @staticmethod
    def transition_to_deleting(status: SkillSyncSourceStatus) -> SkillSyncSourceStatus:
        if not SkillSyncStateMachine.can_delete(status):
            raise ValueError(f"Skill sync source in status '{status}' cannot be deleted")
        return SkillSyncSourceStatus.DELETING


class SkillSyncJobStateMachine:
    @staticmethod
    def transition_to_syncing(status: SkillSyncJobStatus) -> SkillSyncJobStatus:
        if status not in {SkillSyncJobStatus.PENDING, SkillSyncJobStatus.SYNCING}:
            raise ValueError(f"Skill sync job in status '{status}' cannot transition to syncing")
        return SkillSyncJobStatus.SYNCING

    @staticmethod
    def transition_to_success(status: SkillSyncJobStatus) -> SkillSyncJobStatus:
        if status not in {SkillSyncJobStatus.PENDING, SkillSyncJobStatus.SYNCING, SkillSyncJobStatus.SUCCESS}:
            raise ValueError(f"Skill sync job in status '{status}' cannot transition to success")
        return SkillSyncJobStatus.SUCCESS

    @staticmethod
    def transition_to_partial_success(status: SkillSyncJobStatus) -> SkillSyncJobStatus:
        if status not in {
            SkillSyncJobStatus.PENDING,
            SkillSyncJobStatus.SYNCING,
            SkillSyncJobStatus.PARTIAL_SUCCESS,
        }:
            raise ValueError(f"Skill sync job in status '{status}' cannot transition to partial_success")
        return SkillSyncJobStatus.PARTIAL_SUCCESS

    @staticmethod
    def transition_to_failed(status: SkillSyncJobStatus) -> SkillSyncJobStatus:
        if status not in {SkillSyncJobStatus.PENDING, SkillSyncJobStatus.SYNCING, SkillSyncJobStatus.FAILED}:
            raise ValueError(f"Skill sync job in status '{status}' cannot transition to failed")
        return SkillSyncJobStatus.FAILED


class AgentCoreRuntimeAccessMode(StrEnum):
    """Federation-configured auth mode for AgentCore runtime data-plane access."""

    IAM = "iam"
    JWT = "jwt"


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
    SYNCING_VECTORS = "syncing_vectors"
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


class WorkflowNodeType(StrEnum):
    STEP = "step"
    PARALLEL = "parallel"
    LOOP = "loop"
    CONDITION = "condition"
    ROUTER = "router"


# Workflow run status flow:
# PENDING ──→ RUNNING ──→ COMPLETED
#                │
#                ├──→ FAILED
#                ├──→ SKIPPED (on_error="skip" 时)
#                └──→ CANCELLED (workflow 被 cancel)


class WorkflowRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class NodeRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class ResolvedDependencyResolution(StrEnum):
    REUSE_PREVIOUS_OUTPUT = "reuse_previous_output"
    RERUN = "rerun"


class WorkflowDirective(StrEnum):
    """User-issued runtime directives for a live workflow run.

    Covers ad-hoc runtime intervention (pause / resume / cancel / retry) that
    agno does not natively support and we implement via the wait-loop wrapper.
    Node-level HITL decisions (confirm / reject / edit / user_input /
    route_select) flow through ``WorkflowRun.pending_requirements`` +
    ``POST /approve``, not through the directive queue.
    """

    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"
    RETRY = "retry"


# Enums below mirror agno's HumanReview / OnReject / OnTimeout /
# StepRequirement methods so the API/UI layer talks in agno-aligned vocabulary.
class OnRejectPolicy(StrEnum):
    """What should happen when a user rejects an HITL gate.

    Mirrors ``agno.workflow.types.OnReject``.  ``ELSE_BRANCH`` is only valid on
    ``CONDITION`` nodes (sends execution down ``false_steps``).
    """

    SKIP = "skip"
    CANCEL = "cancel"
    RETRY = "retry"
    ELSE_BRANCH = "else_branch"


class OnTimeoutPolicy(StrEnum):
    """What should happen when an HITL gate's timeout expires without a decision.

    Mirrors ``agno.workflow.types.OnTimeout``.
    """

    APPROVE = "approve"
    SKIP = "skip"
    CANCEL = "cancel"


class RequirementResolution(StrEnum):
    """The kind of user decision sent to a pending HITL requirement.

    Maps 1:1 to agno ``StepRequirement.confirm() / .reject() / .edit() /
    .set_user_input() / .set_selected_choices()``.  See API ``POST /approve``.
    """

    CONFIRM = "confirm"
    REJECT = "reject"
    EDIT = "edit"
    USER_INPUT = "user_input"
    ROUTE_SELECT = "route_select"


class WorkflowRunStateMachine:
    """Centralised transition rules for WorkflowRun status changes driven by user directives.

    All callers (API handlers, services) must go through this class instead of
    performing ad-hoc status comparisons so that the rules live in exactly one
    place and can be tested independently.
    """

    # Statuses that can never accept further directives (except idempotent ones).
    TERMINAL_STATUSES: frozenset[WorkflowRunStatus] = frozenset(
        {WorkflowRunStatus.COMPLETED, WorkflowRunStatus.FAILED, WorkflowRunStatus.CANCELLED}
    )

    # Statuses that represent an in-flight execution (can receive pause/cancel).
    ACTIVE_STATUSES: frozenset[WorkflowRunStatus] = frozenset(
        {WorkflowRunStatus.RUNNING, WorkflowRunStatus.PAUSED, WorkflowRunStatus.AWAITING_APPROVAL}
    )

    @staticmethod
    def apply_directive(current: WorkflowRunStatus, directive: WorkflowDirective) -> WorkflowRunStatus:
        """Validate the directive against the current status and return the resulting status.

        Idempotent transitions (pause→pause, cancel→cancel) return ``current`` unchanged.
        Invalid transitions raise ``ValueError`` with a human-readable message so callers
        can translate it directly into an HTTP 400 response.

        Args:
            current:   The run's present status.
            directive: The directive requested by the user.

        Returns:
            The new status the run should transition to.

        Raises:
            ValueError: When the directive is not valid for the current status.
        """
        if directive == WorkflowDirective.PAUSE:
            if current == WorkflowRunStatus.PAUSED:
                return current  # idempotent
            if current == WorkflowRunStatus.RUNNING:
                return WorkflowRunStatus.PAUSED
            raise ValueError(f"Cannot pause a run in status '{current}'")

        if directive == WorkflowDirective.RESUME:
            if current == WorkflowRunStatus.PAUSED:
                return WorkflowRunStatus.RUNNING
            raise ValueError(f"Cannot resume a run in status '{current}'")

        if directive == WorkflowDirective.CANCEL:
            if current == WorkflowRunStatus.CANCELLED:
                return current  # idempotent
            if current in WorkflowRunStateMachine.ACTIVE_STATUSES:
                return WorkflowRunStatus.CANCELLED
            raise ValueError(f"Cannot cancel a run in status '{current}'")

        if directive == WorkflowDirective.RETRY:
            # RETRY creates a child run; the parent status is intentionally unchanged.
            if current in {WorkflowRunStatus.COMPLETED, WorkflowRunStatus.FAILED}:
                return current
            raise ValueError(f"Cannot retry a run in status '{current}'")

        raise ValueError(f"Unknown directive: '{directive}'")


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
