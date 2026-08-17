import type { NodeRunStatus, NodeRunSummary, StepRequirementSummary, WorkflowRunStatusResponse } from './type';
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
