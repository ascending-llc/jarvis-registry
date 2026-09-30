import type {
  GetWorkflowSchedulesResponse,
  NodeRunStatus,
  NodeRunSummary,
  StepRequirementSummary,
  WorkflowPermissionType,
  WorkflowRunStatusResponse,
  WorkflowSchedule,
  WorkflowScheduleInitialInput,
  WorkflowScheduleJsonValue,
} from './type';
import { isWorkflowRunStatus, NODE_RUN_STATUSES } from './type';

const ON_REJECT_VALUES = new Set<StepRequirementSummary['onReject']>(['skip', 'cancel', 'retry', 'else_branch']);

const _asRecord = (value: unknown): Record<string, unknown> | null =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : null;

const _asNonEmptyString = (value: unknown): string | null =>
  typeof value === 'string' && value.trim().length > 0 ? value : null;

const _asOptionalStringOrNull = (value: unknown): string | null | undefined =>
  typeof value === 'string' || value === null ? value : undefined;

const _firstOptionalStringOrNull = (...values: unknown[]): string | null | undefined => {
  for (const value of values) {
    const normalized = _asOptionalStringOrNull(value);
    if (normalized !== undefined) return normalized;
  }
  return undefined;
};

const _isWorkflowScheduleJsonValue = (value: unknown): value is WorkflowScheduleJsonValue => {
  if (value === null || ['boolean', 'number', 'string'].includes(typeof value)) return true;
  if (Array.isArray(value)) return value.every(_isWorkflowScheduleJsonValue);
  const record = _asRecord(value);
  return record !== null && Object.values(record).every(_isWorkflowScheduleJsonValue);
};

const _normalizeInitialInput = (value: unknown): WorkflowScheduleInitialInput | null => {
  if (value === null || value === undefined) return null;
  const record = _asRecord(value);
  if (!record || !Object.values(record).every(_isWorkflowScheduleJsonValue)) {
    throw new Error('Invalid workflow schedule initial input');
  }
  return record as WorkflowScheduleInitialInput;
};

const _normalizePermissions = (value: unknown): WorkflowPermissionType => {
  const raw = _asRecord(value);
  return {
    VIEW: raw?.VIEW === true,
    EDIT: raw?.EDIT === true,
    DELETE: raw?.DELETE === true,
    SHARE: raw?.SHARE === true,
  };
};

const _isNodeRunStatus = (value: unknown): value is NodeRunStatus =>
  typeof value === 'string' && (NODE_RUN_STATUSES as readonly string[]).includes(value);

const _normalizeOnReject = (value: unknown): StepRequirementSummary['onReject'] =>
  typeof value === 'string' && ON_REJECT_VALUES.has(value as StepRequirementSummary['onReject'])
    ? (value as StepRequirementSummary['onReject'])
    : 'skip';

export const normalizeStepRequirementSummary = (value: unknown): StepRequirementSummary | null => {
  const raw = _asRecord(value);
  if (!raw) return null;

  const stepId = _asNonEmptyString(raw.stepId) ?? _asNonEmptyString(raw.step_id);
  if (!stepId) return null;

  const stepNameRaw = raw.stepName ?? raw.step_name;
  const confirmationMessageRaw = raw.confirmationMessage ?? raw.confirmation_message;
  const confirmedRaw = raw.confirmed;

  return {
    stepId,
    stepName: typeof stepNameRaw === 'string' ? stepNameRaw : undefined,
    requiresConfirmation: (raw.requiresConfirmation ?? raw.requires_confirmation) === true,
    confirmationMessage: typeof confirmationMessageRaw === 'string' ? confirmationMessageRaw : undefined,
    confirmed: typeof confirmedRaw === 'boolean' ? confirmedRaw : null,
    onReject: _normalizeOnReject(raw.onReject ?? raw.on_reject),
  };
};

export const normalizePendingRequirements = (value: unknown): StepRequirementSummary[] => {
  if (!Array.isArray(value)) return [];
  return value
    .map(normalizeStepRequirementSummary)
    .filter((requirement): requirement is StepRequirementSummary => requirement !== null);
};

const _normalizeNodeRunSummary = (value: unknown): NodeRunSummary | null => {
  const raw = _asRecord(value);
  if (!raw) return null;

  const nodeId = _asNonEmptyString(raw.nodeId) ?? _asNonEmptyString(raw.node_id);
  const nodeName = _asNonEmptyString(raw.nodeName) ?? _asNonEmptyString(raw.node_name);
  if (!nodeId || !nodeName || !_isNodeRunStatus(raw.status)) return null;

  return {
    nodeId,
    nodeName,
    status: raw.status,
    attempt: typeof raw.attempt === 'number' && Number.isInteger(raw.attempt) && raw.attempt >= 0 ? raw.attempt : 0,
    startedAt: _firstOptionalStringOrNull(raw.startedAt, raw.started_at),
    finishedAt: _firstOptionalStringOrNull(raw.finishedAt, raw.finished_at),
    error: _firstOptionalStringOrNull(raw.error),
  };
};

const _normalizeNodeRuns = (value: unknown): NodeRunSummary[] => {
  if (!Array.isArray(value)) return [];
  return value.map(_normalizeNodeRunSummary).filter((nodeRun): nodeRun is NodeRunSummary => nodeRun !== null);
};

export const normalizeWorkflowRunStatusResponse = (value: unknown): WorkflowRunStatusResponse => {
  const raw = _asRecord(value);
  if (!raw) throw new Error('Invalid workflow run status response');

  const runId = _asNonEmptyString(raw.runId) ?? _asNonEmptyString(raw.run_id);
  const workflowId = _asNonEmptyString(raw.workflowId) ?? _asNonEmptyString(raw.workflow_id);
  const status = raw.status;

  if (!runId || !workflowId || !isWorkflowRunStatus(status)) {
    throw new Error('Invalid workflow run status response');
  }

  const nodeRunsRaw = Array.isArray(raw.nodeRuns) ? raw.nodeRuns : raw.node_runs;

  return {
    runId,
    workflowId,
    status,
    pendingRequirements: normalizePendingRequirements(raw.pendingRequirements ?? raw.pending_requirements),
    nodeRuns: _normalizeNodeRuns(nodeRunsRaw),
  };
};

export const normalizeWorkflowSchedule = (value: unknown): WorkflowSchedule => {
  const raw = _asRecord(value);
  if (!raw) throw new Error('Invalid workflow schedule response');

  const id = _asNonEmptyString(raw.id);
  const workflowDefinitionId =
    _asNonEmptyString(raw.workflowDefinitionId) ?? _asNonEmptyString(raw.workflow_definition_id);
  const cronExpression = _asNonEmptyString(raw.cronExpression) ?? _asNonEmptyString(raw.cron_expression);
  const timezone = _asNonEmptyString(raw.timezone);
  const createdBy = _asNonEmptyString(raw.createdBy) ?? _asNonEmptyString(raw.created_by);
  const createdAt = _asNonEmptyString(raw.createdAt) ?? _asNonEmptyString(raw.created_at);
  const updatedAt = _asNonEmptyString(raw.updatedAt) ?? _asNonEmptyString(raw.updated_at);

  if (
    !id ||
    !workflowDefinitionId ||
    !cronExpression ||
    !timezone ||
    typeof raw.enabled !== 'boolean' ||
    !createdBy ||
    !createdAt ||
    !updatedAt
  ) {
    throw new Error('Invalid workflow schedule response');
  }

  const lastRunStatusRaw = raw.lastRunStatus ?? raw.last_run_status;

  return {
    id,
    workflowDefinitionId,
    cronExpression,
    timezone,
    initialInput: _normalizeInitialInput(raw.initialInput ?? raw.initial_input),
    enabled: raw.enabled,
    nextRunAt: _firstOptionalStringOrNull(raw.nextRunAt, raw.next_run_at) ?? null,
    lastRunAt: _firstOptionalStringOrNull(raw.lastRunAt, raw.last_run_at) ?? null,
    lastRunId: _firstOptionalStringOrNull(raw.lastRunId, raw.last_run_id) ?? null,
    lastRunStatus: isWorkflowRunStatus(lastRunStatusRaw) ? lastRunStatusRaw : null,
    createdBy,
    createdAt,
    updatedAt,
    permissions: _normalizePermissions(raw.permissions),
  };
};

export const normalizeWorkflowSchedulesResponse = (value: unknown): GetWorkflowSchedulesResponse => {
  const raw = _asRecord(value);
  if (!raw || !Array.isArray(raw.items)) throw new Error('Invalid workflow schedules response');

  return {
    items: raw.items.map(normalizeWorkflowSchedule),
    total: typeof raw.total === 'number' && Number.isInteger(raw.total) ? raw.total : raw.items.length,
  };
};
