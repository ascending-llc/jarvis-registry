import { ArrowPathIcon, PencilSquareIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { useCallback, useState } from 'react';
import { CgBrowser } from 'react-icons/cg';
import { FaAws, FaMicrosoft } from 'react-icons/fa';
import { FiClock, FiTag } from 'react-icons/fi';

import { useNavigate } from 'react-router-dom';
import IconButton from '@/components/IconButton';
import { useGlobal } from '@/contexts/GlobalContext';
import { useServer } from '@/contexts/ServerContext';
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

  const [isSyncing, setIsSyncing] = useState(
    federation.syncStatus === 'syncing' || federation.syncStatus === 'pending',
  );

  const handleSyncClick = useCallback(
    async (e: React.MouseEvent) => {
      e.stopPropagation();
      if (!isSyncing) {
        setIsSyncing(true);
        try {
          await SERVICES.FEDERATION.syncFederation(federation.id);
          showToast?.('Sync started successfully', 'success');
          // Wait momentarily for status updates
          setTimeout(() => {
            refreshFederationData();
            setIsSyncing(false);
          }, 2000);
        } catch (err: any) {
          console.error('Failed to sync federation:', err);
          showToast?.(err?.detail?.message || 'Failed to start sync', 'error');
          setIsSyncing(false);
        }
      }
    },
    [federation.id, isSyncing, refreshFederationData, showToast],
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
              isSyncing || federation.syncStatus === 'syncing' || federation.syncStatus === 'pending'
                ? 'bg-[var(--jarvis-info-soft)] text-[var(--jarvis-info-text)]'
                : federation.syncStatus === 'success' || federation.syncStatus === 'idle'
                  ? 'bg-[var(--jarvis-success-soft)] text-[var(--jarvis-success-text)]'
                  : 'bg-[var(--jarvis-danger-soft)] text-[var(--jarvis-danger-text)]'
            }`}
          >
            <span
              className={`w-1.5 h-1.5 rounded-full ${
                isSyncing || federation.syncStatus === 'syncing' || federation.syncStatus === 'pending'
                  ? 'bg-[var(--jarvis-info-text)] animate-pulse'
                  : federation.syncStatus === 'success' || federation.syncStatus === 'idle'
                    ? 'bg-[var(--jarvis-success)]'
                    : 'bg-[var(--jarvis-danger)]'
              }`}
            ></span>
            {isSyncing || federation.syncStatus === 'syncing' || federation.syncStatus === 'pending'
              ? 'Syncing...'
              : federation.syncStatus === 'success' || federation.syncStatus === 'idle'
                ? 'Connected'
                : 'Error'}
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
            tooltip='Sync Now'
            onClick={handleSyncClick}
            disabled={isSyncing}
            size='card'
            className='text-[var(--jarvis-icon)] hover:bg-[var(--jarvis-primary-soft)] hover:text-[var(--jarvis-icon-hover)]'
          >
            <ArrowPathIcon className={`w-3.5 h-3.5 ${isSyncing ? 'animate-spin' : ''}`} />
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
          {federation.syncStatus === 'syncing' || isSyncing
            ? 'Sync in progress...'
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
