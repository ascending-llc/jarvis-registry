import { Dialog } from '@headlessui/react';
import { ArrowPathIcon, KeyIcon, TrashIcon, XMarkIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { useState } from 'react';

import { useGlobal } from '@/contexts/GlobalContext';
import { useServer } from '@/contexts/ServerContext';
import SERVICES from '@/services';
import { SERVER_CONNECTION } from '../services/mcp/type';

interface ServerAuthorizationModalProps {
  name: string;
  serverId: string;
  status: SERVER_CONNECTION | undefined;
  showApiKeyDialog: boolean;
  handleCancelAuth: () => void;
  onCloseAuthDialog: () => void;
}

const ServerAuthorizationModal: React.FC<ServerAuthorizationModalProps> = ({
  name,
  serverId,
  status,
  showApiKeyDialog,
  handleCancelAuth,
  onCloseAuthDialog,
}) => {
  const { showToast } = useGlobal();
  const { refreshServerData, getServerStatusByPolling, cancelPolling } = useServer();

  const [loading, setLoading] = useState(false);

  const isConnecting = status === SERVER_CONNECTION.CONNECTING;
  const isAuthenticated = status === SERVER_CONNECTION.CONNECTED;

  const onClose = () => {
    onCloseAuthDialog();
  };

  const onCancel = () => {
    cancelPolling?.(serverId);
    refreshServerData?.();
    handleCancelAuth();
    onCloseAuthDialog();
  };

  const onClickRevoke = async () => {
    if (isConnecting || isAuthenticated) {
      try {
        setLoading(true);
        const result = await SERVICES.MCP.revokeAuth(serverId);
        if (result.success) {
          showToast?.(result?.message || 'OAuth flow cancelled', 'success');
        } else {
          showToast?.(result?.message || 'Unknown error', 'error');
        }
      } catch (error) {
        showToast?.(error instanceof Error ? error.message : 'Unknown error', 'error');
      } finally {
        setLoading(false);
        refreshServerData?.();
      }
    }
    onCloseAuthDialog();
  };

  const oauthInit = async () => {
    try {
      setLoading(true);
      const result = await SERVICES.MCP.getOauthInitiate(serverId);
      if (result?.authorizationUrl) {
        window.open(result.authorizationUrl, '_blank');
        getServerStatusByPolling?.(serverId);
        onCloseAuthDialog();
      } else {
        showToast?.('Failed to get auth URL', 'error');
      }
    } catch (error) {
      showToast?.(error instanceof Error ? error.message : 'Unknown error', 'error');
    } finally {
      setLoading(false);
    }
  };

  const handleAuth = async () => {
    try {
      setLoading(true);
      const result = await SERVICES.MCP.getOauthReinit(serverId);
      if (result.success) {
        await getServerStatusByPolling?.(serverId, state => {
          if (state === SERVER_CONNECTION.CONNECTED) {
            showToast?.(result?.message || 'Server reinitialized successfully', 'success');
            onCloseAuthDialog();
          } else if (state === SERVER_CONNECTION.DISCONNECTED || state === SERVER_CONNECTION.ERROR) {
            oauthInit();
          }
        });
      } else {
        oauthInit();
      }
    } catch (_error) {
      oauthInit();
    } finally {
      setLoading(false);
    }
  };

  return (
    <Dialog
      open={showApiKeyDialog}
      onClose={onCloseAuthDialog}
      className='fixed inset-0 z-50 flex items-center justify-center p-4'
    >
      <div className='fixed inset-0 bg-black/50' aria-hidden='true' />
      <div className='w-[512px] h-[140px] bg-white dark:bg-gray-800 shadow-xl rounded-lg p-6 relative'>
        <div className='flex items-start justify-between mb-6'>
          <div className='flex items-center gap-4'>
            <h3 className='text-xl font-semibold text-gray-900 dark:text-white'>{name}</h3>
            {isConnecting ? (
              <div className='flex items-center gap-1.5 text-sm text-blue-500'>
                <div className='animate-spin rounded-full h-3 w-3 border-b-2 border-slate-600' />
                Connecting
              </div>
            ) : isAuthenticated ? (
              <div className='flex items-center gap-1.5 text-sm text-green-500'>
                <div className='w-2.5 h-2.5 rounded-full bg-emerald-400 shadow-lg shadow-emerald-400/30' />
                Authenticated
              </div>
            ) : (
              <span className='flex items-center gap-1 px-2 py-0.5 bg-amber-50 dark:bg-amber-300 text-amber-600 dark:text-amber-600 rounded-full text-xs font-medium'>
                <KeyIcon className='h-3 w-3 dark:text-amber-600' />
                OAuth
              </span>
            )}
          </div>
          <button onClick={onClose} className='text-gray-400 hover:text-gray-600 dark:hover:text-gray-300'>
            <XMarkIcon className='h-6 w-6' />
          </button>
        </div>

        <div className='flex gap-2'>
          {isConnecting && (
            <button
              className='px-3 h-10 border-0 text-gray-700 dark:text-gray-300 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 disabled:bg-gray-500 text-sm rounded-lg cursor-pointer flex items-center justify-center gap-2'
              disabled={loading}
              onClick={onCancel}
            >
              Cancel
            </button>
          )}
          {isAuthenticated && (
            <button
              className='px-3 h-10 border-0 text-gray-700 dark:text-gray-300 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 disabled:bg-gray-500 text-sm rounded-lg cursor-pointer flex items-center justify-center gap-2'
              disabled={loading}
              onClick={onClickRevoke}
            >
              {loading ? (
                <div className='animate-spin rounded-full h-4 w-4 border-b-2 border-slate-200' />
              ) : (
                <TrashIcon className='h-4 w-4' />
              )}
              Revoke
            </button>
          )}
          {!isConnecting && (
            <button
              className='btn-primary flex-1 h-10 text-white font-medium rounded-lg border-0 cursor-pointer flex items-center justify-center gap-2 text-sm transition-colors'
              disabled={loading}
              onClick={handleAuth}
            >
              {loading && <div className='animate-spin rounded-full h-4 w-4 border-b-2 border-slate-200' />}
              {!(loading || isAuthenticated) && <ArrowPathIcon className='h-4 w-4' />}
              {isAuthenticated ? 'Reconnect' : 'Authenticate'}
            </button>
          )}
        </div>
      </div>
    </Dialog>
  );
};

export default ServerAuthorizationModal;
