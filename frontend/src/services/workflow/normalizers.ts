import type { StepRequirementSummary, WorkflowRunStatusResponse } from './type';
import { isWorkflowRunStatus } from './type';

const ON_REJECT_VALUES = new Set<StepRequirementSummary['onReject']>(['skip', 'cancel', 'retry', 'else_branch']);

const _asRecord = (value: unknown): Record<string, unknown> | null =>
  value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;

const _asNonEmptyString = (value: unknown): string | null =>
  typeof value === 'string' && value.trim().length > 0 ? value : null;

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

export const normalizeWorkflowRunStatusResponse = (value: unknown): WorkflowRunStatusResponse => {
  const raw = _asRecord(value);
  if (!raw) throw new Error('Invalid workflow run status response');

  const runId = _asNonEmptyString(raw.runId) ?? _asNonEmptyString(raw.run_id);
  const workflowId = _asNonEmptyString(raw.workflowId) ?? _asNonEmptyString(raw.workflow_id);
  const status = raw.status;

  if (!runId || !workflowId || !isWorkflowRunStatus(status)) {
    throw new Error('Invalid workflow run status response');
  }

  return {
    runId,
    workflowId,
    status,
    pendingRequirements: normalizePendingRequirements(raw.pendingRequirements ?? raw.pending_requirements),
  };
};
