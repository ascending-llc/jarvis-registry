import { useCallback, useEffect, useRef, useState } from 'react';

import { useGlobal } from '@/contexts/GlobalContext';
import {
  type FederationSyncViewState,
  getFederationSyncViewState,
  useFederationSyncPolling,
} from '@/hooks/useFederationSyncPolling';
import SERVICES from '@/services';
import { confirmGithubAuthorizationRedirect } from '@/services/externalProvider/githubAuthorization';
import { getSkillSyncJobAsFederation } from '@/services/externalProvider/sync';
import type { FederationSyncJobStatus, SyncStatus } from '@/services/federation/type';
import { getErrorMessage } from '@/utils/getErrorMessage';

export const GITHUB_SYNC_NEEDS_AUTHORIZATION_MESSAGE = 'GitHub authorization is required before syncing.';

interface UseExternalProviderSyncOptions {
  providerId: string | null;
  isGithub: boolean;
  /** EDIT permission; without it `runSyncAction` does nothing (the backend enforces it too). */
  canEdit: boolean;
  serverStatus?: SyncStatus;
  syncMessage?: string | null;
  /** The job to resume polling while the server reports pending/syncing. */
  serverJobId?: string | null;
  /** Reload provider data after a job finishes or on a "Refresh Status" action. */
  onSettled: () => void;
}

interface StartSyncOptions {
  /** When false, a `needsAuthorization` answer shows an error instead of offering the GitHub redirect. */
  allowAuthorizationRedirect?: boolean;
}

interface UseExternalProviderSyncReturn {
  syncView: FederationSyncViewState;
  isPolling: boolean;
  startSync: (options?: StartSyncOptions) => Promise<boolean>;
  runSyncAction: () => void;
  stopPolling: () => void;
}

/** Starts a sync job; resolves to the job id, or null when GitHub authorization is needed first. */
const _requestSyncJobId = async (providerId: string, isGithub: boolean): Promise<string | null> => {
  if (isGithub) {
    const result = await SERVICES.SKILL_SYNC_SOURCE.syncSkillSyncSource(providerId, { dryRun: false });
    if (result.needsAuthorization) return null;
    if (!result.job) throw new Error('Failed to start sync');
    return result.job.id;
  }

  const job = await SERVICES.FEDERATION.syncFederation(providerId);
  if (!('id' in job)) throw new Error('Failed to start sync');
  return job.id;
};

const _isSyncInProgress = (status?: SyncStatus): boolean => status === 'pending' || status === 'syncing';

/**
 * Sync state and actions for one external provider (AWS, Azure or GitHub), shared by the provider
 * card and the provider view page: status label, Sync Now / Retry Status / Refresh Status, polling,
 * and the GitHub authorization prompt.
 */
export const useExternalProviderSync = ({
  providerId,
  isGithub,
  canEdit,
  serverStatus,
  syncMessage,
  serverJobId,
  onSettled,
}: UseExternalProviderSyncOptions): UseExternalProviderSyncReturn => {
  const { showToast } = useGlobal();
  const [isStarting, setIsStarting] = useState(false);
  const providerIdRef = useRef(providerId);
  const onSettledRef = useRef(onSettled);
  const requestPendingRef = useRef(false);
  const requestGenerationRef = useRef(0);
  providerIdRef.current = providerId;
  onSettledRef.current = onSettled;

  const handleTerminal = useCallback(
    (job: FederationSyncJobStatus) => {
      if (job.federationId !== providerIdRef.current) return;
      if (job.status === 'success') showToast?.('Sync completed successfully', 'success');
      else if (job.status === 'partial_success') showToast?.('Sync completed with some errors', 'info');
      else showToast?.(job.error || 'Sync failed', 'error');
      onSettledRef.current();
    },
    [showToast],
  );

  const { jobStatus, isPolling, pollingError, startPolling, retryPolling, stopPolling } = useFederationSyncPolling(
    handleTerminal,
    isGithub ? getSkillSyncJobAsFederation : undefined,
  );

  // A different provider invalidates any sync request still in flight for the previous one.
  useEffect(() => {
    requestGenerationRef.current += 1;
    requestPendingRef.current = false;
    setIsStarting(false);
  }, [providerId]);

  const serverSyncInProgress = _isSyncInProgress(serverStatus);
  useEffect(() => {
    if (providerId && serverJobId && serverSyncInProgress) {
      startPolling(providerId, serverJobId);
      return;
    }
    stopPolling();
  }, [providerId, serverJobId, serverSyncInProgress, startPolling, stopPolling]);

  const syncView = getFederationSyncViewState({
    serverStatus,
    syncMessage,
    hasServerJobId: Boolean(serverJobId),
    isStarting,
    isPolling,
    pollingError,
    jobStatus,
  });

  const startSync = useCallback(
    async ({ allowAuthorizationRedirect = true }: StartSyncOptions = {}): Promise<boolean> => {
      if (!providerId || requestPendingRef.current || isPolling) return false;

      requestPendingRef.current = true;
      const requestGeneration = ++requestGenerationRef.current;
      setIsStarting(true);
      try {
        const jobId = await _requestSyncJobId(providerId, isGithub);
        if (requestGeneration !== requestGenerationRef.current) return false;
        if (jobId === null) {
          const redirected = allowAuthorizationRedirect && confirmGithubAuthorizationRedirect(providerId, 'sync');
          if (!redirected) showToast?.(GITHUB_SYNC_NEEDS_AUTHORIZATION_MESSAGE, 'error');
          return false;
        }

        showToast?.('Sync started in background', 'info');
        startPolling(providerId, jobId);
        return true;
      } catch (error: unknown) {
        if (requestGeneration !== requestGenerationRef.current) return false;
        showToast?.(getErrorMessage(error, 'Failed to start sync'), 'error');
        return false;
      } finally {
        if (requestGeneration === requestGenerationRef.current) {
          requestPendingRef.current = false;
          setIsStarting(false);
        }
      }
    },
    [isGithub, isPolling, providerId, showToast, startPolling],
  );

  const runSyncAction = useCallback(() => {
    if (!canEdit) return;
    if (syncView.action === 'retry') retryPolling();
    else if (syncView.action === 'refresh') onSettledRef.current();
    else if (syncView.action === 'start') void startSync();
  }, [canEdit, retryPolling, startSync, syncView.action]);

  return { syncView, isPolling, startSync, runSyncAction, stopPolling };
};
