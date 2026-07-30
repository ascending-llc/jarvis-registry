import { useCallback, useEffect, useRef, useState } from 'react';

import SERVICES from '@/services';
import {
  TERMINAL_RUN_STATUSES,
  type GetWorkflowRunsListResponse,
  type WorkflowRunStatusResponse,
} from '@/services/workflow/type';

const POLL_INTERVAL_MS = 5000;
const MAX_CONSECUTIVE_POLL_FAILURES = 5;

interface UseActiveWorkflowRunResult {
  isLocked: boolean;
  activeRun: WorkflowRunStatusResponse | null;
  trackRun: (runId: string) => void;
  refetchNow: () => Promise<void>;
}

interface TrackedRun {
  workflowId: string;
  runId: string;
}

interface DiscoveryState {
  workflowId: string | undefined;
  pending: boolean;
}

const _emptyRefetch = async (): Promise<void> => undefined;
const _getStatusRequestKey = (workflowId: string, runId: string): string => `${workflowId}:${runId}`;
const _deleteRequestWhenSettled = <T,>(
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
  const [discoveryState, setDiscoveryState] = useState<DiscoveryState>({
    workflowId,
    pending: Boolean(workflowId),
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

  const adoptRun = useCallback((nextRun: TrackedRun | null) => {
    setActiveRun(null);
    setTrackedRun(nextRun);
  }, []);

  const getDiscoveryRequest = useCallback((currentWorkflowId: string): Promise<GetWorkflowRunsListResponse> => {
    const currentRequest = discoveryRequestsRef.current.get(currentWorkflowId);
    if (currentRequest) return currentRequest;

    const promise = SERVICES.WORKFLOW.getWorkflowRunsList(currentWorkflowId, { perPage: 1 });
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
      setDiscoveryState({ workflowId, pending: false });
      adoptRun({ workflowId, runId });
    },
    [adoptRun, workflowId],
  );

  useEffect(() => {
    const generation = ++discoveryGenerationRef.current;
    adoptRun(null);
    setDiscoveryState({ workflowId, pending: Boolean(workflowId) });

    if (!workflowId) return;

    const discover = async () => {
      try {
        const response = await getDiscoveryRequest(workflowId);
        if (generation !== discoveryGenerationRef.current) return;

        const newestRun = response.runs[0];
        if (!newestRun || TERMINAL_RUN_STATUSES.has(newestRun.status)) return;
        adoptRun({ workflowId, runId: newestRun.id });
      } catch {
        if (generation !== discoveryGenerationRef.current) return;
        onPollErrorRef.current('Failed to check active workflow run');
      } finally {
        if (generation === discoveryGenerationRef.current) {
          setDiscoveryState({ workflowId, pending: false });
        }
      }
    };

    void discover();

    return () => {
      if (generation === discoveryGenerationRef.current) {
        discoveryGenerationRef.current += 1;
      }
    };
  }, [adoptRun, getDiscoveryRequest, workflowId]);

  useEffect(() => {
    const generation = ++pollGenerationRef.current;
    pollNowRef.current = _emptyRefetch;
    setActiveRun(null);

    if (!workflowId || !trackedRun || trackedRun.workflowId !== workflowId) return;

    let timeout: ReturnType<typeof setTimeout> | null = null;
    let inFlight: Promise<void> | null = null;
    let queuedRefetch: Promise<void> | null = null;
    let consecutiveFailures = 0;
    let stopped = false;

    const clearScheduledPoll = () => {
      if (timeout !== null) {
        clearTimeout(timeout);
        timeout = null;
      }
    };

    const scheduleNextPoll = () => {
      if (stopped || generation !== pollGenerationRef.current) return;
      timeout = setTimeout(() => {
        timeout = null;
        void pollOnce();
      }, POLL_INTERVAL_MS);
    };

    const executePoll = async () => {
      try {
        const status = await getStatusRequest(workflowId, trackedRun.runId);
        if (stopped || generation !== pollGenerationRef.current) return;

        consecutiveFailures = 0;
        setActiveRun(status);

        if (TERMINAL_RUN_STATUSES.has(status.status)) {
          stopped = true;
          clearScheduledPoll();
          adoptRun(null);
          return;
        }

        scheduleNextPoll();
      } catch {
        if (stopped || generation !== pollGenerationRef.current) return;

        consecutiveFailures += 1;
        if (consecutiveFailures >= MAX_CONSECUTIVE_POLL_FAILURES) {
          stopped = true;
          clearScheduledPoll();
          adoptRun(null);
          onPollErrorRef.current('Failed to refresh workflow run status');
          return;
        }

        scheduleNextPoll();
      }
    };

    function pollOnce(): Promise<void> {
      clearScheduledPoll();
      if (stopped || generation !== pollGenerationRef.current) return _emptyRefetch();
      if (inFlight) return inFlight;

      const request: Promise<void> = executePoll().finally(() => {
        if (inFlight === request) inFlight = null;
      });
      inFlight = request;
      return request;
    }

    const refetchOnce = (): Promise<void> => {
      clearScheduledPoll();
      if (!inFlight) return pollOnce();
      if (queuedRefetch) return queuedRefetch;

      queuedRefetch = inFlight.then(() => pollOnce()).finally(() => {
        queuedRefetch = null;
      });
      return queuedRefetch;
    };

    pollNowRef.current = refetchOnce;
    void pollOnce();

    return () => {
      stopped = true;
      clearScheduledPoll();
      if (generation === pollGenerationRef.current) pollGenerationRef.current += 1;
      if (pollNowRef.current === refetchOnce) pollNowRef.current = _emptyRefetch;
    };
  }, [adoptRun, getStatusRequest, trackedRun, workflowId]);

  const refetchNow = useCallback((): Promise<void> => pollNowRef.current(), []);
  const hasCurrentRun = trackedRun !== null && trackedRun.workflowId === workflowId;
  const isDiscoveringCurrentWorkflow =
    Boolean(workflowId) && (discoveryState.workflowId !== workflowId || discoveryState.pending);

  return {
    isLocked: hasCurrentRun || isDiscoveringCurrentWorkflow,
    activeRun: hasCurrentRun && activeRun?.workflowId === workflowId ? activeRun : null,
    trackRun,
    refetchNow,
  };
};
