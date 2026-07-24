import { useCallback, useEffect, useRef, useState } from 'react';

import SERVICES from '@/services';
import type { FederationSyncJobStatus, JobPhase, SyncStatus } from '@/services/federation/type';

const POLL_INTERVAL_MS = 4000;
const MAX_CONSECUTIVE_ERRORS = 5;
const MAX_POLL_DURATION_MS = 30 * 60 * 1000;

const PHASE_LABELS: Record<JobPhase, string> = {
  queued: 'Starting sync...',
  discovering: 'Syncing...',
  applying: 'Applying changes...',
  syncing_vectors: 'Updating search index...',
  completed: 'Sync completed',
  failed: 'Sync failed',
};

export type FederationSyncPollingError = 'request_failed' | 'timeout' | 'invalid_status';

export type FederationSyncViewKind = 'idle' | 'starting' | 'running' | 'success' | 'failed' | 'unavailable';
export type FederationSyncAction = 'start' | 'retry' | 'refresh' | 'none';

export interface FederationSyncViewState {
  kind: FederationSyncViewKind;
  label: string;
  detail: string | null;
  tone: 'info' | 'success' | 'error';
  isBusy: boolean;
  action: FederationSyncAction;
  actionLabel: string;
}

interface FederationSyncViewStateInput {
  serverStatus?: SyncStatus;
  syncMessage?: string | null;
  hasServerJobId: boolean;
  isStarting: boolean;
  isPolling: boolean;
  pollingError: FederationSyncPollingError | null;
  jobStatus: FederationSyncJobStatus | null;
}

interface PollingTarget {
  federationId: string;
  jobId: string;
}

interface UseFederationSyncPollingReturn {
  jobStatus: FederationSyncJobStatus | null;
  isPolling: boolean;
  pollingError: FederationSyncPollingError | null;
  startPolling: (federationId: string, jobId: string) => void;
  retryPolling: () => void;
  stopPolling: () => void;
}

const _isSameTarget = (left: PollingTarget | null, right: PollingTarget): boolean =>
  left?.federationId === right.federationId && left.jobId === right.jobId;

export const getFederationSyncPhaseLabel = (phase?: JobPhase | null): string =>
  phase ? (PHASE_LABELS[phase] ?? 'Syncing...') : 'Syncing...';

export const getFederationSyncPollingErrorLabel = (error: FederationSyncPollingError): string => {
  if (error === 'timeout') return 'Sync is taking longer than expected';
  if (error === 'invalid_status') return 'Unsupported sync status';
  return 'Status unavailable';
};

export const getFederationSyncViewState = ({
  serverStatus,
  syncMessage,
  hasServerJobId,
  isStarting,
  isPolling,
  pollingError,
  jobStatus,
}: FederationSyncViewStateInput): FederationSyncViewState => {
  if (pollingError) {
    return {
      kind: 'unavailable',
      label: getFederationSyncPollingErrorLabel(pollingError),
      detail: null,
      tone: 'error',
      isBusy: false,
      action: 'retry',
      actionLabel: 'Retry Status',
    };
  }

  if (isStarting) {
    return {
      kind: 'starting',
      label: 'Starting sync...',
      detail: null,
      tone: 'info',
      isBusy: true,
      action: 'none',
      actionLabel: 'Sync Now',
    };
  }

  if (isPolling) {
    return {
      kind: 'running',
      label: getFederationSyncPhaseLabel(jobStatus?.phase),
      detail: null,
      tone: 'info',
      isBusy: true,
      action: 'none',
      actionLabel: 'Sync Now',
    };
  }

  if (jobStatus?.status === 'success') {
    return {
      kind: 'success',
      label: 'Sync completed',
      detail: null,
      tone: 'success',
      isBusy: false,
      action: 'start',
      actionLabel: 'Sync Now',
    };
  }

  if (jobStatus?.status === 'failed') {
    return {
      kind: 'failed',
      label: 'Sync failed',
      detail: jobStatus.error || syncMessage || null,
      tone: 'error',
      isBusy: false,
      action: 'start',
      actionLabel: 'Sync Now',
    };
  }

  const serverReportsSyncInProgress = serverStatus === 'pending' || serverStatus === 'syncing';
  if (serverReportsSyncInProgress && !hasServerJobId) {
    return {
      kind: 'unavailable',
      label: 'Sync task information unavailable',
      detail: null,
      tone: 'error',
      isBusy: false,
      action: 'refresh',
      actionLabel: 'Refresh Status',
    };
  }

  if (serverReportsSyncInProgress) {
    return {
      kind: 'running',
      label: 'Syncing...',
      detail: null,
      tone: 'info',
      isBusy: true,
      action: 'none',
      actionLabel: 'Sync Now',
    };
  }

  if (serverStatus === 'failed') {
    return {
      kind: 'failed',
      label: 'Error',
      detail: syncMessage || null,
      tone: 'error',
      isBusy: false,
      action: 'start',
      actionLabel: 'Sync Now',
    };
  }

  return {
    kind: 'idle',
    label: 'Connected',
    detail: null,
    tone: 'success',
    isBusy: false,
    action: 'start',
    actionLabel: 'Sync Now',
  };
};

export const getFederationSyncErrorMessage = (error: unknown, fallback: string): string => {
  if (!error || typeof error !== 'object') return fallback;

  if ('detail' in error) {
    const detail = error.detail;
    if (typeof detail === 'string') return detail;
    if (detail && typeof detail === 'object' && 'message' in detail && typeof detail.message === 'string') {
      return detail.message;
    }
  }

  if ('message' in error && typeof error.message === 'string') return error.message;
  return fallback;
};

export const useFederationSyncPolling = (
  onTerminal?: (job: FederationSyncJobStatus) => void,
): UseFederationSyncPollingReturn => {
  const [jobStatus, setJobStatus] = useState<FederationSyncJobStatus | null>(null);
  const [isPolling, setIsPolling] = useState(false);
  const [pollingError, setPollingError] = useState<FederationSyncPollingError | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const errorCountRef = useRef(0);
  const pollingStartedAtRef = useRef(0);
  const pollingGenerationRef = useRef(0);
  const activeTargetRef = useRef<PollingTarget | null>(null);
  const retryTargetRef = useRef<PollingTarget | null>(null);
  const terminalTargetRef = useRef<PollingTarget | null>(null);
  const isMountedRef = useRef(true);
  const onTerminalRef = useRef(onTerminal);
  onTerminalRef.current = onTerminal;

  const cancelActivePolling = useCallback(() => {
    pollingGenerationRef.current += 1;
    activeTargetRef.current = null;
    if (timeoutRef.current !== null) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
  }, []);

  const stopPolling = useCallback(() => {
    cancelActivePolling();
    retryTargetRef.current = null;
    terminalTargetRef.current = null;
    errorCountRef.current = 0;
    setJobStatus(null);
    setIsPolling(false);
    setPollingError(null);
  }, [cancelActivePolling]);

  const failPolling = useCallback(
    (error: FederationSyncPollingError) => {
      cancelActivePolling();
      setIsPolling(false);
      setPollingError(error);
    },
    [cancelActivePolling],
  );

  const startPolling = useCallback(
    (federationId: string, jobId: string) => {
      if (!isMountedRef.current) return;

      const target = { federationId, jobId };
      if (_isSameTarget(activeTargetRef.current, target)) return;
      if (_isSameTarget(terminalTargetRef.current, target)) return;

      const isRetryingTarget = _isSameTarget(retryTargetRef.current, target);
      cancelActivePolling();
      const pollingGeneration = pollingGenerationRef.current;
      activeTargetRef.current = target;
      retryTargetRef.current = target;
      terminalTargetRef.current = null;
      errorCountRef.current = 0;
      pollingStartedAtRef.current = Date.now();
      if (!isRetryingTarget) setJobStatus(null);
      setIsPolling(true);
      setPollingError(null);

      const poll = async () => {
        if (pollingGeneration !== pollingGenerationRef.current) return;
        if (Date.now() - pollingStartedAtRef.current >= MAX_POLL_DURATION_MS) {
          failPolling('timeout');
          return;
        }

        const requestController = new AbortController();
        abortControllerRef.current = requestController;

        try {
          const job = await SERVICES.FEDERATION.getFederationSyncJob(federationId, jobId, {
            signal: requestController.signal,
          });
          if (abortControllerRef.current === requestController) abortControllerRef.current = null;
          if (pollingGeneration !== pollingGenerationRef.current) return;

          const status = String(job.status);
          if (!['pending', 'syncing', 'success', 'failed'].includes(status)) {
            failPolling('invalid_status');
            return;
          }

          errorCountRef.current = 0;
          setJobStatus(job);

          if (status === 'success' || status === 'failed') {
            cancelActivePolling();
            retryTargetRef.current = null;
            terminalTargetRef.current = target;
            setIsPolling(false);
            setPollingError(null);
            onTerminalRef.current?.(job);
            return;
          }

          timeoutRef.current = setTimeout(() => {
            timeoutRef.current = null;
            void poll();
          }, POLL_INTERVAL_MS);
        } catch {
          if (abortControllerRef.current === requestController) abortControllerRef.current = null;
          if (pollingGeneration !== pollingGenerationRef.current) return;

          errorCountRef.current += 1;
          if (errorCountRef.current >= MAX_CONSECUTIVE_ERRORS) {
            failPolling('request_failed');
            return;
          }
          timeoutRef.current = setTimeout(() => {
            timeoutRef.current = null;
            void poll();
          }, POLL_INTERVAL_MS);
        }
      };

      void poll();
    },
    [cancelActivePolling, failPolling],
  );

  const retryPolling = useCallback(() => {
    const target = retryTargetRef.current;
    if (target) startPolling(target.federationId, target.jobId);
  }, [startPolling]);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      cancelActivePolling();
    };
  }, [cancelActivePolling]);

  return {
    jobStatus,
    isPolling,
    pollingError,
    startPolling,
    retryPolling,
    stopPolling,
  };
};
