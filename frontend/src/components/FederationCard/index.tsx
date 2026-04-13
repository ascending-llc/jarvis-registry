import type React from 'react';
import { useCallback, useState } from 'react';
import { CgBrowser } from 'react-icons/cg';
import { FaAws, FaMicrosoft } from 'react-icons/fa';
import { FiClock, FiEdit2, FiRefreshCw, FiTag } from 'react-icons/fi';

import { useNavigate } from 'react-router-dom';
import { useGlobal } from '@/contexts/GlobalContext';
import { useServer } from '@/contexts/ServerContext';
import SERVICES from '@/services';
import type { Federation } from '@/services/federation/type';

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

  const formatDistanceToNow = (dateString?: string | null) => {
    if (!dateString) return 'Never';
    // Simple mock formatted relative time. In a real project you might use date-fns `formatDistanceToNowStrict`
    const date = new Date(dateString);
    const now = new Date();
    const diffInSeconds = Math.floor((now.getTime() - date.getTime()) / 1000);
    if (diffInSeconds < 60) return 'Just now';
    if (diffInSeconds < 3600) return `${Math.floor(diffInSeconds / 60)} min ago`;
    if (diffInSeconds < 86400) return `${Math.floor(diffInSeconds / 3600)} hours ago`;
    return `${Math.floor(diffInSeconds / 86400)} days ago`;
  };
  
  return (
    <div className='bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl p-5 mb-3 transition-colors hover:border-gray-300 dark:hover:border-gray-600 group'>
      {/* Header */}
      <div className='flex items-start justify-between mb-3'>
        <div className='flex items-center gap-3'>
          <div
            className={`w-10 h-10 rounded-lg flex items-center justify-center shrink-0 ${
              isAws
                ? 'bg-amber-100 dark:bg-amber-500/15 text-amber-500'
                : isAzure
                  ? 'bg-blue-100 dark:bg-blue-500/15 text-blue-500'
                  : 'bg-gray-100 dark:bg-gray-500/15 text-gray-400'
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
              className='text-base font-semibold text-gray-900 dark:text-white cursor-pointer hover:text-purple-600 dark:hover:text-purple-400 transition-colors truncate'
              onClick={handleViewClick}
              title={federation.displayName}
            >
              {federation.displayName}
            </div>
            <div className='text-sm text-gray-500 dark:text-gray-400 mt-0.5'>
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
                ? 'bg-blue-100 dark:bg-blue-500/15 text-blue-600 dark:text-blue-400'
                : federation.syncStatus === 'success' || federation.syncStatus === 'idle'
                  ? 'bg-emerald-100 dark:bg-emerald-500/15 text-emerald-600 dark:text-emerald-400'
                  : 'bg-red-100 dark:bg-red-500/15 text-red-600 dark:text-red-400'
            }`}
          >
            <span
              className={`w-1.5 h-1.5 rounded-full ${
                isSyncing || federation.syncStatus === 'syncing' || federation.syncStatus === 'pending'
                  ? 'bg-blue-500 animate-pulse'
                  : federation.syncStatus === 'success' || federation.syncStatus === 'idle'
                    ? 'bg-emerald-500'
                    : 'bg-red-500'
              }`}
            ></span>
            {isSyncing || federation.syncStatus === 'syncing' || federation.syncStatus === 'pending'
              ? 'Syncing...'
              : federation.syncStatus === 'success' || federation.syncStatus === 'idle'
                ? 'Connected'
                : 'Error'}
          </div>

          <button
            onClick={handleEditClick}
            title='Edit'
            className='w-8 h-8 flex items-center justify-center bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-500 dark:text-gray-300 transition-colors hover:bg-gray-200 dark:hover:bg-gray-600 hover:text-gray-700 dark:hover:text-white'
          >
            <FiEdit2 className='w-3.5 h-3.5' />
          </button>
          <button
            onClick={handleSyncClick}
            disabled={isSyncing}
            title='Sync Now'
            className={`w-8 h-8 flex items-center justify-center bg-gray-100 dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-lg text-gray-500 dark:text-gray-300 transition-colors ${
              isSyncing
                ? 'opacity-50 cursor-not-allowed'
                : 'hover:bg-gray-200 dark:hover:bg-gray-600 hover:text-gray-700 dark:hover:text-white'
            }`}
          >
            <FiRefreshCw className={`w-3.5 h-3.5 ${isSyncing ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {/* Meta Bar */}
      <div className='flex flex-wrap gap-4 text-xs text-gray-500 dark:text-gray-400 mb-3'>
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
            : `Last synced: ${formatDistanceToNow(federation.lastSync?.finishedAt)}`}
        </span>
      </div>

      {/* Stats */}
      <div className='grid grid-cols-3 gap-3 border-t border-gray-200 dark:border-gray-700 pt-3 mt-3'>
        <div className='bg-gray-50 dark:bg-gray-900/50 rounded-lg p-3 text-center'>
          <div className='text-xl font-bold text-purple-600 dark:text-purple-400'>
            {federation.status === 'active' && federation.stats ? federation.stats.mcpServerCount : '—'}
          </div>
          <div className='text-[11px] text-gray-500 mt-0.5'>MCP Servers</div>
        </div>
        <div className='bg-gray-50 dark:bg-gray-900/50 rounded-lg p-3 text-center'>
          <div className='text-xl font-bold text-emerald-600 dark:text-emerald-400'>
            {federation.status === 'active' && federation.stats ? federation.stats.agentCount : '—'}
          </div>
          <div className='text-[11px] text-gray-500 mt-0.5'>AI Agents</div>
        </div>
        <div className='bg-gray-50 dark:bg-gray-900/50 rounded-lg p-3 text-center'>
          <div className='text-xl font-bold text-blue-600 dark:text-blue-400'>
            {federation.status === 'active' && federation.stats ? federation.stats.importedTotal : '—'}
          </div>
          <div className='text-[11px] text-gray-500 mt-0.5'>Total Imported</div>
        </div>
      </div>
    </div>
  );
};

export default FederationCard;
