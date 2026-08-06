import { useCallback, useEffect, useRef, useState } from 'react';

import SERVICES from '@/services';
import {
  type GetWorkflowRunsListResponse,
  TERMINAL_RUN_STATUSES,
  type WorkflowRunStatusResponse,
} from '@/services/workflow/type';

import { getFailureRetryDelayMs, getPollingIntervalMs, MAX_MONITORING_SESSION_MS } from './pollingPolicy';

export type WorkflowRunMonitoringState = 'idle' | 'discovering' | 'polling' | 'hidden' | 'stale';

// Discovery stays bounded to one request while covering the backend's maximum page size.
const ACTIVE_RUN_DISCOVERY_PAGE_SIZE = 100;

interface UseActiveWorkflowRunResult {
  isLocked: boolean;
  isMonitoringActive: boolean;
  monitoringState: WorkflowRunMonitoringState;
  activeRun: WorkflowRunStatusResponse | null;
  trackRun: (runId: string) => void;
  refetchNow: () => Promise<void>;
}

interface TrackedRun {
  workflowId: string;
  runId: string;
  sessionStartedAtMs: number;
}

interface MonitoringSnapshot {
  workflowId: string | undefined;
  state: WorkflowRunMonitoringState;
}

const _emptyRefetch = async (): Promise<void> => undefined;
const _getStatusRequestKey = (workflowId: string, runId: string): string => `${workflowId}:${runId}`;
const _isDocumentHidden = (): boolean => typeof document !== 'undefined' && document.visibilityState === 'hidden';
const _deleteRequestWhenSettled = <T>(
  requests: Map<string, Promise<T>>,
  requestKey: string,
  promise: Promise<T>,
): void => {
  const deleteCurrentRequest = () => {
    if (requests.get(requestKey) === promise) requests.delete(requestKey);
  };
  promise.then(deleteCurrentRequest, deleteCurrentRequest);
};

export const useActiveWorkflowRun = (
  workflowId: string | undefined,
  onPollError: (message: string) => void,
): UseActiveWorkflowRunResult => {
  const [trackedRun, setTrackedRun] = useState<TrackedRun | null>(null);
  const [activeRun, setActiveRun] = useState<WorkflowRunStatusResponse | null>(null);
  const [monitoringSnapshot, setMonitoringSnapshot] = useState<MonitoringSnapshot>({
    workflowId,
    state: workflowId ? 'discovering' : 'idle',
  });
  const discoveryGenerationRef = useRef(0);
  const pollGenerationRef = useRef(0);
  const currentWorkflowIdRef = useRef(workflowId);
  const discoveryRequestsRef = useRef(new Map<string, Promise<GetWorkflowRunsListResponse>>());
  const statusRequestsRef = useRef(new Map<string, Promise<WorkflowRunStatusResponse>>());
  const pollNowRef = useRef<() => Promise<void>>(_emptyRefetch);
  const onPollErrorRef = useRef(onPollError);
  currentWorkflowIdRef.current = workflowId;
  onPollErrorRef.current = onPollError;

  const clearRun = useCallback((currentWorkflowId: string | undefined) => {
    setActiveRun(null);
    setTrackedRun(null);
    setMonitoringSnapshot({ workflowId: currentWorkflowId, state: 'idle' });
  }, []);

  const adoptRun = useCallback((nextRun: TrackedRun) => {
    setActiveRun(null);
    setTrackedRun(nextRun);
    setMonitoringSnapshot({
      workflowId: nextRun.workflowId,
      state: _isDocumentHidden() ? 'hidden' : 'polling',
    });
  }, []);

  const getDiscoveryRequest = useCallback((currentWorkflowId: string): Promise<GetWorkflowRunsListResponse> => {
    const currentRequest = discoveryRequestsRef.current.get(currentWorkflowId);
    if (currentRequest) return currentRequest;

    const promise = SERVICES.WORKFLOW.getWorkflowRunsList(currentWorkflowId, {
      perPage: ACTIVE_RUN_DISCOVERY_PAGE_SIZE,
    });
    discoveryRequestsRef.current.set(currentWorkflowId, promise);
    _deleteRequestWhenSettled(discoveryRequestsRef.current, currentWorkflowId, promise);
    return promise;
  }, []);

  const getStatusRequest = useCallback(
    (currentWorkflowId: string, runId: string): Promise<WorkflowRunStatusResponse> => {
      const requestKey = _getStatusRequestKey(currentWorkflowId, runId);
      const currentRequest = statusRequestsRef.current.get(requestKey);
      if (currentRequest) return currentRequest;

      const promise = SERVICES.WORKFLOW.getWorkflowRunStatus(currentWorkflowId, runId);
      statusRequestsRef.current.set(requestKey, promise);
      _deleteRequestWhenSettled(statusRequestsRef.current, requestKey, promise);
      return promise;
    },
    [],
  );

  const trackRun = useCallback(
    (runId: string) => {
      if (!workflowId || currentWorkflowIdRef.current !== workflowId) return;
      discoveryGenerationRef.current += 1;
      pollGenerationRef.current += 1;
      adoptRun({ workflowId, runId, sessionStartedAtMs: Date.now() });
    },
    [adoptRun, workflowId],
  );

  useEffect(() => {
    const generation = ++discoveryGenerationRef.current;
    setActiveRun(null);
    setTrackedRun(null);
    setMonitoringSnapshot({ workflowId, state: workflowId ? 'discovering' : 'idle' });

    if (!workflowId) return;

    const discover = async () => {
      try {
        const response = await getDiscoveryRequest(workflowId);
        if (generation !== discoveryGenerationRef.current || currentWorkflowIdRef.current !== workflowId) return;

        const newestActiveRun = response.runs.find(run => !TERMINAL_RUN_STATUSES.has(run.status));
        if (!newestActiveRun) {
          clearRun(workflowId);
          return;
        }
        adoptRun({ workflowId, runId: newestActiveRun.id, sessionStartedAtMs: Date.now() });
      } catch {
        if (generation !== discoveryGenerationRef.current || currentWorkflowIdRef.current !== workflowId) return;
        setActiveRun(null);
        setTrackedRun(null);
        setMonitoringSnapshot({ workflowId, state: 'stale' });
        onPollErrorRef.current('Unable to verify the workflow run status. Reload the page to try again.');
      }
    };

    void discover();

    return () => {
      if (generation === discoveryGenerationRef.current) {
        discoveryGenerationRef.current += 1;
      }
    };
  }, [adoptRun, clearRun, getDiscoveryRequest, workflowId]);

  useEffect(() => {
    const generation = ++pollGenerationRef.current;
    pollNowRef.current = _emptyRefetch;
    setActiveRun(null);

    if (!workflowId || !trackedRun || trackedRun.workflowId !== workflowId) return;

    let timeout: ReturnType<typeof setTimeout> | null = null;
    let inFlight: Promise<void> | null = null;
    let queuedRefetch: Promise<void> | null = null;
    let resolveQueuedRefetch: (() => void) | null = null;
    let consecutiveFailures = 0;
    let stopped = false;
    let mode: WorkflowRunMonitoringState = _isDocumentHidden() ? 'hidden' : 'polling';
    let staleErrorReported = false;

    const isCurrentGeneration = () =>
      !stopped && generation === pollGenerationRef.current && currentWorkflowIdRef.current === workflowId;

    const setMode = (nextMode: WorkflowRunMonitoringState) => {
      mode = nextMode;
      setMonitoringSnapshot({ workflowId, state: nextMode });
    };

    const clearScheduledPoll = () => {
      if (timeout !== null) {
        clearTimeout(timeout);
        timeout = null;
      }
    };

    const enterStale = (message: string) => {
      if (!isCurrentGeneration()) return;
      clearScheduledPoll();
      setMode('stale');
      if (staleErrorReported) return;
      staleErrorReported = true;
      onPollErrorRef.current(message);
    };

    const completeQueuedRefetch = () => {
      resolveQueuedRefetch?.();
      resolveQueuedRefetch = null;
      queuedRefetch = null;
    };

    const scheduleNextPoll = (delayMs: number) => {
      if (!isCurrentGeneration() || mode !== 'polling') return;
      const elapsedMs = Date.now() - trackedRun.sessionStartedAtMs;
      const remainingMs = Math.max(0, MAX_MONITORING_SESSION_MS - elapsedMs);
      const isFinalCheck = delayMs >= remainingMs;
      timeout = setTimeout(
        () => {
          timeout = null;
          void pollOnce(isFinalCheck);
        },
        Math.min(delayMs, remainingMs),
      );
    };

    const executePoll = async (isFinalCheck: boolean) => {
      try {
        const status = await getStatusRequest(workflowId, trackedRun.runId);
        if (!isCurrentGeneration()) return;

        consecutiveFailures = 0;
        setActiveRun(status);

        if (TERMINAL_RUN_STATUSES.has(status.status)) {
          stopped = true;
          clearScheduledPoll();
          clearRun(workflowId);
          return;
        }

        if (mode === 'stale') return;
        if (isFinalCheck || Date.now() - trackedRun.sessionStartedAtMs >= MAX_MONITORING_SESSION_MS) {
          enterStale('Workflow run monitoring reached the 60-minute limit. Reload the page to check its status.');
          return;
        }
        if (_isDocumentHidden() || mode === 'hidden') {
          setMode('hidden');
          return;
        }

        const elapsedMs = Date.now() - trackedRun.sessionStartedAtMs;
        scheduleNextPoll(getPollingIntervalMs(status.status, elapsedMs));
      } catch {
        if (!isCurrentGeneration()) return;
        if (mode === 'stale') return;
        if (isFinalCheck) {
          enterStale(
            'Failed to refresh workflow run status at the 60-minute limit. Reload the page to check its status.',
          );
          return;
        }

        consecutiveFailures += 1;
        const retryDelayMs = getFailureRetryDelayMs(consecutiveFailures);
        if (retryDelayMs === null) {
          enterStale('Failed to refresh workflow run status. Reload the page to retry.');
          return;
        }
        if (_isDocumentHidden() || mode === 'hidden') {
          setMode('hidden');
          return;
        }
        scheduleNextPoll(retryDelayMs);
      }
    };

    const flushQueuedRefetch = () => {
      if (!queuedRefetch || inFlight || _isDocumentHidden() || mode === 'hidden') return;

      const resolveCurrentRefetch = resolveQueuedRefetch;
      queuedRefetch = null;
      resolveQueuedRefetch = null;
      const shouldFinalCheck =
        mode !== 'stale' && Date.now() - trackedRun.sessionStartedAtMs >= MAX_MONITORING_SESSION_MS;
      void pollOnce(shouldFinalCheck).then(
        () => resolveCurrentRefetch?.(),
        () => resolveCurrentRefetch?.(),
      );
    };

    function pollOnce(isFinalCheck = false): Promise<void> {
      clearScheduledPoll();
      if (!isCurrentGeneration()) return _emptyRefetch();
      if (_isDocumentHidden() || mode === 'hidden') return _emptyRefetch();
      if (inFlight) return inFlight;

      const request: Promise<void> = executePoll(isFinalCheck).finally(() => {
        if (inFlight === request) inFlight = null;
        if (isCurrentGeneration()) flushQueuedRefetch();
      });
      inFlight = request;
      return request;
    }

    const refetchOnce = (): Promise<void> => {
      clearScheduledPoll();
      if (!isCurrentGeneration()) return _emptyRefetch();
      if (!_isDocumentHidden() && mode !== 'hidden' && !inFlight) {
        const shouldFinalCheck =
          mode !== 'stale' && Date.now() - trackedRun.sessionStartedAtMs >= MAX_MONITORING_SESSION_MS;
        return pollOnce(shouldFinalCheck);
      }
      if (queuedRefetch) return queuedRefetch;

      queuedRefetch = new Promise<void>(resolve => {
        resolveQueuedRefetch = resolve;
      });
      return queuedRefetch;
    };

    const handleVisibilityChange = () => {
      if (!isCurrentGeneration()) return;
      if (_isDocumentHidden()) {
        clearScheduledPoll();
        if (mode !== 'stale') setMode('hidden');
        return;
      }
      if (mode === 'stale') {
        flushQueuedRefetch();
        return;
      }

      setMode('polling');
      const shouldFinalCheck = Date.now() - trackedRun.sessionStartedAtMs >= MAX_MONITORING_SESSION_MS;
      if (inFlight) {
        if (!queuedRefetch) {
          queuedRefetch = new Promise<void>(resolve => {
            resolveQueuedRefetch = resolve;
          });
        }
        return;
      }
      if (queuedRefetch) {
        flushQueuedRefetch();
        return;
      }
      void pollOnce(shouldFinalCheck);
    };

    pollNowRef.current = refetchOnce;
    setMode(mode);
    if (typeof document !== 'undefined') document.addEventListener('visibilitychange', handleVisibilityChange);
    if (mode === 'polling') void pollOnce();

    return () => {
      stopped = true;
      clearScheduledPoll();
      completeQueuedRefetch();
      if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', handleVisibilityChange);
      if (generation === pollGenerationRef.current) pollGenerationRef.current += 1;
      if (pollNowRef.current === refetchOnce) pollNowRef.current = _emptyRefetch;
    };
  }, [clearRun, getStatusRequest, trackedRun, workflowId]);

  const refetchNow = useCallback((): Promise<void> => pollNowRef.current(), []);
  const hasCurrentRun = trackedRun !== null && trackedRun.workflowId === workflowId;
  const monitoringState =
    monitoringSnapshot.workflowId === workflowId ? monitoringSnapshot.state : workflowId ? 'discovering' : 'idle';

  return {
    isLocked: monitoringState !== 'idle',
    isMonitoringActive: monitoringState === 'polling',
    monitoringState,
    activeRun: hasCurrentRun && activeRun?.workflowId === workflowId ? activeRun : null,
    trackRun,
    refetchNow,
  };
};
