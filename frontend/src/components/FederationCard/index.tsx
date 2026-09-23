import { ArrowPathIcon, PencilSquareIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { CgBrowser } from 'react-icons/cg';
import { FaAws, FaGithub, FaMicrosoft } from 'react-icons/fa';
import { FiClock, FiTag } from 'react-icons/fi';
import { useNavigate } from 'react-router-dom';

import IconButton from '@/components/IconButton';
import { useGlobal } from '@/contexts/GlobalContext';
import { useServer } from '@/contexts/ServerContext';
import {
  getFederationSyncErrorMessage,
  getFederationSyncViewState,
  useFederationSyncPolling,
} from '@/hooks/useFederationSyncPolling';
import SERVICES from '@/services';
import { saveGithubOauthIntent } from '@/services/externalProvider/oauthIntent';
import { getSkillSyncJobAsFederation, getSkillSyncSourceOauthUrl } from '@/services/externalProvider/sync';
import type { ExternalProviderEntity } from '@/services/externalProvider/type';
import UTILS from '@/utils';

interface FederationCardProps {
  externalProvider: ExternalProviderEntity;
}

const FederationCard: React.FC<FederationCardProps> = ({ externalProvider }) => {
  const navigate = useNavigate();
  const { showToast } = useGlobal();
  const { refreshFederationData } = useServer();
  const [isStartingSync, setIsStartingSync] = useState(false);
  const syncRequestPendingRef = useRef(false);
  const syncRequestGenerationRef = useRef(0);

  const isGithub = externalProvider.backendKind === 'skill-sync-source';
  const provider = externalProvider.data;
  const isAws = provider.providerType === 'aws_agentcore';
  const isAzure = provider.providerType === 'azure_ai_foundry';
  const lastSync = provider.lastSync;
  const canEdit = provider.permissions.EDIT;

  const { jobStatus, isPolling, pollingError, startPolling, retryPolling, stopPolling } = useFederationSyncPolling(
    job => {
      if (job.status === 'success') showToast?.('Sync completed successfully', 'success');
      else if (job.status === 'partial_success') showToast?.('Sync completed with some errors', 'info');
      else showToast?.(job.error || 'Sync failed', 'error');
      void refreshFederationData();
    },
    isGithub ? getSkillSyncJobAsFederation : undefined,
  );

  useEffect(() => {
    const jobId = lastSync?.jobId;
    if (jobId && (provider.syncStatus === 'pending' || provider.syncStatus === 'syncing')) {
      startPolling(provider.id, jobId);
      return;
    }
    stopPolling();
  }, [lastSync?.jobId, provider.id, provider.syncStatus, startPolling, stopPolling]);

  useEffect(
    () => () => {
      syncRequestGenerationRef.current += 1;
      syncRequestPendingRef.current = false;
    },
    [],
  );

  const syncView = getFederationSyncViewState({
    serverStatus: provider.syncStatus,
    syncMessage: provider.syncMessage,
    hasServerJobId: Boolean(lastSync?.jobId),
    isStarting: isStartingSync,
    isPolling,
    pollingError,
    jobStatus,
  });

  const handleSyncClick = useCallback(
    async (event: React.MouseEvent) => {
      event.stopPropagation();
      if (!canEdit || syncRequestPendingRef.current || isPolling) return;

      if (syncView.action === 'retry') {
        retryPolling();
        return;
      }
      if (syncView.action === 'refresh') {
        void refreshFederationData();
        return;
      }
      if (syncView.action === 'none') return;

      syncRequestPendingRef.current = true;
      const syncRequestGeneration = ++syncRequestGenerationRef.current;
      setIsStartingSync(true);

      try {
        if (isGithub) {
          const result = await SERVICES.SKILL_SYNC_SOURCE.syncSkillSyncSource(provider.id, { dryRun: false });
          if (syncRequestGeneration !== syncRequestGenerationRef.current) return;
          if (result.needsAuthorization) {
            saveGithubOauthIntent(provider.id, 'sync');
            window.location.assign(getSkillSyncSourceOauthUrl(provider.id));
            return;
          }
          if (!result.job) throw new Error('Failed to start sync');
          showToast?.('Sync started in background', 'info');
          startPolling(provider.id, result.job.id);
          return;
        }

        const job = await SERVICES.FEDERATION.syncFederation(provider.id);
        if (syncRequestGeneration !== syncRequestGenerationRef.current) return;
        if (!('id' in job)) throw new Error('Failed to start sync');
        showToast?.('Sync started in background', 'info');
        startPolling(provider.id, job.id);
      } catch (error: unknown) {
        if (syncRequestGeneration !== syncRequestGenerationRef.current) return;
        console.error('Failed to sync external provider:', error);
        showToast?.(getFederationSyncErrorMessage(error, 'Failed to start sync'), 'error');
      } finally {
        if (syncRequestGeneration === syncRequestGenerationRef.current) {
          syncRequestPendingRef.current = false;
          setIsStartingSync(false);
        }
      }
    },
    [
      canEdit,
      isGithub,
      isPolling,
      provider.id,
      refreshFederationData,
      retryPolling,
      showToast,
      startPolling,
      syncView.action,
    ],
  );

  const handleEditClick = useCallback(
    (event: React.MouseEvent) => {
      event.stopPropagation();
      navigate(`/federation-edit?id=${encodeURIComponent(provider.id)}${isGithub ? '&provider=github' : ''}`);
    },
    [isGithub, navigate, provider.id],
  );

  const handleViewClick = useCallback(() => {
    navigate(
      `/federation-registry?id=${encodeURIComponent(provider.id)}&isReadOnly=true${isGithub ? '&provider=github' : ''}`,
    );
  }, [isGithub, navigate, provider.id]);

  const providerSubtitle = isAws ? 'Amazon Web Services' : isAzure ? 'Microsoft Azure' : 'GitHub';
  const repositoryLabel =
    externalProvider.backendKind === 'skill-sync-source'
      ? `${externalProvider.data.owner}/${externalProvider.data.repo}`
      : null;
  const lastSyncLabel = UTILS.formatTimeSince(lastSync?.finishedAt) ?? 'Never';

  return (
    <div className='group mb-3 rounded-xl border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] p-5 shadow-sm transition-all duration-300 hover:-translate-y-1 hover:border-[color:var(--jarvis-border-strong)] hover:shadow-xl'>
      <div className='mb-3 flex items-start justify-between'>
        <div className='flex items-center gap-3'>
          <div
            className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg ${
              isAws
                ? 'bg-[var(--jarvis-warning-soft)] text-[var(--jarvis-warning-text)]'
                : isAzure
                  ? 'bg-[var(--jarvis-info-soft)] text-[var(--jarvis-info-text)]'
                  : isGithub
                    ? 'bg-[var(--jarvis-card-muted)] text-[var(--jarvis-text)]'
                    : 'bg-[var(--jarvis-card-muted)] text-[var(--jarvis-muted)]'
            }`}
          >
            {isAws ? (
              <FaAws className='h-6 w-6' />
            ) : isAzure ? (
              <FaMicrosoft className='h-5 w-5' />
            ) : isGithub ? (
              <FaGithub className='h-5 w-5' />
            ) : (
              <CgBrowser className='h-5 w-5' />
            )}
          </div>
          <div className='min-w-0 flex-1'>
            <button
              type='button'
              className='block max-w-full truncate text-left text-base font-semibold text-[var(--jarvis-text)] transition-colors hover:text-[var(--jarvis-text-strong)]'
              onClick={handleViewClick}
              title={provider.displayName}
            >
              {provider.displayName}
            </button>
            <div className='mt-0.5 text-sm text-[var(--jarvis-muted)]'>
              {providerSubtitle}
              {externalProvider.backendKind === 'federation' &&
                externalProvider.data.providerConfig?.region &&
                ` · ${externalProvider.data.providerConfig.region}`}
            </div>
          </div>
        </div>
        <div className='flex items-center gap-2'>
          <div
            title={syncView.detail ?? undefined}
            className={`flex items-center gap-1.5 rounded-md px-2.5 py-1 text-xs font-medium ${
              syncView.tone === 'info'
                ? 'bg-[var(--jarvis-info-soft)] text-[var(--jarvis-info-text)]'
                : syncView.tone === 'error'
                  ? 'bg-[var(--jarvis-danger-soft)] text-[var(--jarvis-danger-text)]'
                  : 'bg-[var(--jarvis-success-soft)] text-[var(--jarvis-success-text)]'
            }`}
          >
            <span
              className={`h-1.5 w-1.5 rounded-full ${
                syncView.tone === 'info'
                  ? 'animate-pulse bg-[var(--jarvis-info-text)]'
                  : syncView.tone === 'error'
                    ? 'bg-[var(--jarvis-danger)]'
                    : 'bg-[var(--jarvis-success)]'
              }`}
            />
            {syncView.label}
          </div>

          {canEdit && (
            <IconButton
              ariaLabel='Edit external provider'
              tooltip='Edit'
              onClick={handleEditClick}
              size='card'
              className='text-[var(--jarvis-icon)] hover:bg-[var(--jarvis-primary-soft)] hover:text-[var(--jarvis-icon-hover)]'
            >
              <PencilSquareIcon className='h-3.5 w-3.5' />
            </IconButton>
          )}
          <IconButton
            ariaLabel='Sync external provider'
            tooltip={canEdit ? syncView.actionLabel : 'No edit permission'}
            onClick={handleSyncClick}
            disabled={!canEdit || syncView.action === 'none'}
            size='card'
            className='text-[var(--jarvis-icon)] hover:bg-[var(--jarvis-primary-soft)] hover:text-[var(--jarvis-icon-hover)]'
          >
            <ArrowPathIcon className={`h-3.5 w-3.5 ${syncView.isBusy ? 'animate-spin' : ''}`} />
          </IconButton>
        </div>
      </div>

      <div className='mb-3 flex flex-wrap gap-4 text-xs text-[var(--jarvis-muted)]'>
        {externalProvider.backendKind === 'federation' && externalProvider.data.providerConfig?.assumeRoleArn && (
          <span className='flex items-center gap-1.5'>
            <FiTag className='h-3.5 w-3.5' />
            <span className='max-w-[200px] truncate sm:max-w-xs'>
              {externalProvider.data.providerConfig.assumeRoleArn}
            </span>
          </span>
        )}
        {repositoryLabel && (
          <span className='flex items-center gap-1.5'>
            <FiTag className='h-3.5 w-3.5' />
            <span className='max-w-[200px] truncate sm:max-w-xs'>{repositoryLabel}</span>
          </span>
        )}
        <span className='flex items-center gap-1.5'>
          <FiClock className='h-3.5 w-3.5' />
          {syncView.kind !== 'idle' ? syncView.label : `Last synced: ${lastSyncLabel}`}
        </span>
      </div>

      {externalProvider.backendKind === 'skill-sync-source' ? (
        <div className='mt-3 grid grid-cols-2 gap-3 border-t border-[color:var(--jarvis-border)] pt-3'>
          <div className='rounded-lg bg-[var(--jarvis-card-muted)] p-3 text-center'>
            <div className='text-xl font-bold text-[var(--jarvis-primary-text)]'>
              {externalProvider.data.status === 'active' ? externalProvider.data.stats.skillCount : '—'}
            </div>
            <div className='mt-0.5 text-[11px] text-[var(--jarvis-subtle)]'>Skills</div>
          </div>
          <div className='rounded-lg bg-[var(--jarvis-card-muted)] p-3 text-center'>
            <div className='text-xl font-bold text-[var(--jarvis-info-text)]'>
              {externalProvider.data.status === 'active' ? externalProvider.data.stats.fileCount : '—'}
            </div>
            <div className='mt-0.5 text-[11px] text-[var(--jarvis-subtle)]'>Files</div>
          </div>
        </div>
      ) : (
        <div className='mt-3 grid grid-cols-4 gap-3 border-t border-[color:var(--jarvis-border)] pt-3'>
          <div className='rounded-lg bg-[var(--jarvis-card-muted)] p-3 text-center'>
            <div className='text-xl font-bold text-[var(--jarvis-primary-text)]'>
              {externalProvider.data.status === 'active' && externalProvider.data.stats
                ? externalProvider.data.stats.mcpServerCount
                : '—'}
            </div>
            <div className='mt-0.5 text-[11px] text-[var(--jarvis-subtle)]'>MCP Servers</div>
          </div>
          <div className='rounded-lg bg-[var(--jarvis-card-muted)] p-3 text-center'>
            <div className='text-xl font-bold text-[var(--jarvis-success-text)]'>
              {externalProvider.data.status === 'active' && externalProvider.data.stats
                ? externalProvider.data.stats.agentCount
                : '—'}
            </div>
            <div className='mt-0.5 text-[11px] text-[var(--jarvis-subtle)]'>AI Agents</div>
          </div>
          <div className='rounded-lg bg-[var(--jarvis-card-muted)] p-3 text-center'>
            <div className='text-xl font-bold text-[var(--jarvis-info-text)]'>
              {externalProvider.data.status === 'active' && externalProvider.data.stats
                ? externalProvider.data.stats.importedTotal
                : '—'}
            </div>
            <div className='mt-0.5 text-[11px] text-[var(--jarvis-subtle)]'>Total Imported</div>
          </div>
          <div className='rounded-lg bg-[var(--jarvis-card-muted)] p-3 text-center'>
            <div className='text-xl font-bold text-[var(--jarvis-danger-text)]'>
              {externalProvider.data.status === 'active' && externalProvider.data.stats
                ? externalProvider.data.stats.unimportedTotal
                : '—'}
            </div>
            <div className='mt-0.5 text-[11px] text-[var(--jarvis-subtle)]'>Total Unimported</div>
          </div>
        </div>
      )}
    </div>
  );
};

export default FederationCard;
