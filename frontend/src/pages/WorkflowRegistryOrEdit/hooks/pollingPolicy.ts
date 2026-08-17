import type { WorkflowRunStatus } from '@/services/workflow/type';

export const FAST_POLLING_WINDOW_MS = 60_000;
export const MEDIUM_POLLING_WINDOW_MS = 5 * 60_000;
export const FAST_POLLING_INTERVAL_MS = 5_000;
export const MEDIUM_POLLING_INTERVAL_MS = 10_000;
export const SLOW_POLLING_INTERVAL_MS = 30_000;
export const MAX_MONITORING_SESSION_MS = 60 * 60_000;

export const FAILURE_RETRY_DELAYS_MS = [5_000, 10_000, 20_000, 40_000, 60_000] as const;

export const getPollingIntervalMs = (status: WorkflowRunStatus, elapsedMs: number): number => {
  if (status === 'paused' || status === 'awaiting_approval') return SLOW_POLLING_INTERVAL_MS;
  if (elapsedMs < FAST_POLLING_WINDOW_MS) return FAST_POLLING_INTERVAL_MS;
  if (elapsedMs < MEDIUM_POLLING_WINDOW_MS) return MEDIUM_POLLING_INTERVAL_MS;
  return SLOW_POLLING_INTERVAL_MS;
};

export const getFailureRetryDelayMs = (consecutiveFailureCount: number): number | null =>
  FAILURE_RETRY_DELAYS_MS[consecutiveFailureCount - 1] ?? null;
