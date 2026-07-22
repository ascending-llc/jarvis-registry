import { ArrowPathIcon, PencilSquareIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { CgBrowser } from 'react-icons/cg';
import { FaAws, FaMicrosoft } from 'react-icons/fa';
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
import type { Federation } from '@/services/federation/type';
import UTILS from '@/utils';

interface FederationCardProps {
  federation: Federation;
}

const FederationCard: React.FC<FederationCardProps> = ({ federation }) => {
  const navigate = useNavigate();
  const { showToast } = useGlobal();
  const { refreshFederationData } = useServer();
  const [isStartingSync, setIsStartingSync] = useState(false);
  const syncRequestPendingRef = useRef(false);
  const syncRequestGenerationRef = useRef(0);

  const { jobStatus, isPolling, pollingError, startPolling, retryPolling, stopPolling } = useFederationSyncPolling(
    job => {
      if (job.status === 'success') {
        showToast?.('Sync completed successfully', 'success');
      } else {
        showToast?.(job.error || 'Sync failed', 'error');
      }
      refreshFederationData();
    },
  );

  useEffect(() => {
    const jobId = federation.lastSync?.jobId;
    if (jobId && (federation.syncStatus === 'pending' || federation.syncStatus === 'syncing')) {
      startPolling(federation.id, jobId);
      return;
    }
    stopPolling();
  }, [federation.id, federation.lastSync?.jobId, federation.syncStatus, startPolling, stopPolling]);

  useEffect(
    () => () => {
      syncRequestGenerationRef.current += 1;
      syncRequestPendingRef.current = false;
    },
    [],
  );

  const syncView = getFederationSyncViewState({
    serverStatus: federation.syncStatus,
    hasServerJobId: Boolean(federation.lastSync?.jobId),
    isStarting: isStartingSync,
    isPolling,
    pollingError,
    jobStatus,
  });

  const handleSyncClick = useCallback(
    async (e: React.MouseEvent) => {
      e.stopPropagation();
      if (syncRequestPendingRef.current || isPolling) return;

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
      showToast?.('Sync started in background', 'info');

      try {
        const job = await SERVICES.FEDERATION.syncFederation(federation.id);
        if (syncRequestGeneration !== syncRequestGenerationRef.current) return;
        if (!('id' in job)) throw new Error('Failed to start sync');
        startPolling(federation.id, job.id);
      } catch (error: unknown) {
        if (syncRequestGeneration !== syncRequestGenerationRef.current) return;
        console.error('Failed to sync federation:', error);
        showToast?.(getFederationSyncErrorMessage(error, 'Failed to start sync'), 'error');
      } finally {
        if (syncRequestGeneration === syncRequestGenerationRef.current) {
          syncRequestPendingRef.current = false;
          setIsStartingSync(false);
        }
      }
    },
    [federation.id, isPolling, refreshFederationData, retryPolling, showToast, startPolling, syncView.action],
  );

  const handleEditClick = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      navigate(`/federation-edit?id=${federation.id}`);
    },
    [federation.id, navigate],
  );

  const handleViewClick = useCallback(() => {
    navigate(`/federation-registry?id=${federation.id}&isReadOnly=true`);
  }, [federation.id, navigate]);

  const isAws = federation.providerType === 'aws_agentcore';
  const isAzure = federation.providerType === 'azure_ai_foundry';

  return (
    <div className='group mb-3 rounded-xl border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] p-5 transition-all duration-300 hover:border-[color:var(--jarvis-border-strong)] shadow-sm hover:shadow-xl hover:-translate-y-1'>
      {/* Header */}
      <div className='flex items-start justify-between mb-3'>
        <div className='flex items-center gap-3'>
          <div
            className={`w-10 h-10 rounded-lg flex items-center justify-center shrink-0 ${
              isAws
                ? 'bg-[var(--jarvis-warning-soft)] text-[var(--jarvis-warning-text)]'
                : isAzure
                  ? 'bg-[var(--jarvis-info-soft)] text-[var(--jarvis-info-text)]'
                  : 'bg-[var(--jarvis-card-muted)] text-[var(--jarvis-muted)]'
            }`}
          >
            {isAws ? (
              <FaAws className='w-6 h-6' />
            ) : isAzure ? (
              <FaMicrosoft className='w-5 h-5' />
            ) : (
              <CgBrowser className='w-5 h-5' />
            )}
          </div>
          <div className='flex-1 min-w-0'>
            <div
              className='truncate cursor-pointer text-base font-semibold text-[var(--jarvis-text)] transition-colors hover:text-[var(--jarvis-text-strong)]'
              onClick={handleViewClick}
              title={federation.displayName}
            >
              {federation.displayName}
            </div>
            <div className='mt-0.5 text-sm text-[var(--jarvis-muted)]'>
              {isAws ? 'Amazon Web Services' : isAzure ? 'Microsoft Azure' : 'Unknown Provider'}
              {federation.providerConfig?.region && ` · ${federation.providerConfig.region}`}
            </div>
          </div>
        </div>
        <div className='flex items-center gap-2'>
          {/* Status Badge */}
          <div
            className={`flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-md ${
              syncView.tone === 'info'
                ? 'bg-[var(--jarvis-info-soft)] text-[var(--jarvis-info-text)]'
                : syncView.tone === 'error'
                  ? 'bg-[var(--jarvis-danger-soft)] text-[var(--jarvis-danger-text)]'
                  : 'bg-[var(--jarvis-success-soft)] text-[var(--jarvis-success-text)]'
            }`}
          >
            <span
              className={`w-1.5 h-1.5 rounded-full ${
                syncView.tone === 'info'
                  ? 'bg-[var(--jarvis-info-text)] animate-pulse'
                  : syncView.tone === 'error'
                    ? 'bg-[var(--jarvis-danger)]'
                    : 'bg-[var(--jarvis-success)]'
              }`}
            ></span>
            {syncView.label}
          </div>

          <IconButton
            ariaLabel='Edit federation'
            tooltip='Edit'
            onClick={handleEditClick}
            size='card'
            className='text-[var(--jarvis-icon)] hover:bg-[var(--jarvis-primary-soft)] hover:text-[var(--jarvis-icon-hover)]'
          >
            <PencilSquareIcon className='w-3.5 h-3.5' />
          </IconButton>
          <IconButton
            ariaLabel='Sync federation'
            tooltip={syncView.actionLabel}
            onClick={handleSyncClick}
            disabled={syncView.action === 'none'}
            size='card'
            className='text-[var(--jarvis-icon)] hover:bg-[var(--jarvis-primary-soft)] hover:text-[var(--jarvis-icon-hover)]'
          >
            <ArrowPathIcon className={`w-3.5 h-3.5 ${syncView.isBusy ? 'animate-spin' : ''}`} />
          </IconButton>
        </div>
      </div>

      {/* Meta Bar */}
      <div className='mb-3 flex flex-wrap gap-4 text-xs text-[var(--jarvis-muted)]'>
        {federation.providerConfig?.assumeRoleArn && (
          <span className='flex items-center gap-1.5'>
            <FiTag className='w-3.5 h-3.5' />
            <span className='truncate max-w-[200px] sm:max-w-xs'>{federation.providerConfig.assumeRoleArn}</span>
          </span>
        )}
        <span className='flex items-center gap-1.5'>
          <FiClock className='w-3.5 h-3.5' />
          {syncView.kind !== 'idle'
            ? syncView.label
            : `Last synced: ${UTILS.formatTimeSince(federation.lastSync?.finishedAt) ?? 'Never'}`}
        </span>
      </div>

      {/* Stats */}
      <div className='mt-3 grid grid-cols-3 gap-3 border-t border-[color:var(--jarvis-border)] pt-3'>
        <div className='rounded-lg bg-[var(--jarvis-card-muted)] p-3 text-center'>
          <div className='text-xl font-bold text-[var(--jarvis-primary-text)]'>
            {federation.status === 'active' && federation.stats ? federation.stats.mcpServerCount : '—'}
          </div>
          <div className='mt-0.5 text-[11px] text-[var(--jarvis-subtle)]'>MCP Servers</div>
        </div>
        <div className='rounded-lg bg-[var(--jarvis-card-muted)] p-3 text-center'>
          <div className='text-xl font-bold text-[var(--jarvis-success-text)]'>
            {federation.status === 'active' && federation.stats ? federation.stats.agentCount : '—'}
          </div>
          <div className='mt-0.5 text-[11px] text-[var(--jarvis-subtle)]'>AI Agents</div>
        </div>
        <div className='rounded-lg bg-[var(--jarvis-card-muted)] p-3 text-center'>
          <div className='text-xl font-bold text-[var(--jarvis-info-text)]'>
            {federation.status === 'active' && federation.stats ? federation.stats.importedTotal : '—'}
          </div>
          <div className='mt-0.5 text-[11px] text-[var(--jarvis-subtle)]'>Total Imported</div>
        </div>
      </div>
    </div>
  );
};

export default FederationCard;
