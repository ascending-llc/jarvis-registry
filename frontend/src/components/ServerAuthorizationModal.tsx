import { Dialog } from '@headlessui/react';
import { ArrowPathIcon, KeyIcon, TrashIcon, XMarkIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { useState } from 'react';

import IconButton from '@/components/IconButton';
import { useGlobal } from '@/contexts/GlobalContext';
import { useServer } from '@/contexts/ServerContext';
import SERVICES from '@/services';
import { ServerConnection } from '../services/mcp/type';

interface ServerAuthorizationModalProps {
  name: string;
  serverId: string;
  status: ServerConnection | undefined;
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

  const isConnecting = status === ServerConnection.CONNECTING;
  const isAuthenticated = status === ServerConnection.CONNECTED;

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
          if (state === ServerConnection.CONNECTED) {
            showToast?.(result?.message || 'Server reinitialized successfully', 'success');
            onCloseAuthDialog();
          } else if (state === ServerConnection.DISCONNECTED || state === ServerConnection.ERROR) {
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
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
    >
      <div className="fixed inset-0 bg-black/50" aria-hidden='true' />
      <div className="w-[512px] h-[140px] bg-[var(--jarvis-card)] bg-[var(--jarvis-card)] shadow-xl rounded-lg p-6 relative">
        <div className="flex items-start justify-between mb-6">
          <div className="flex items-center gap-4">
            <h3 className="text-xl font-semibold text-[var(--jarvis-text-strong)] text-[var(--jarvis-text-strong)]">{name}</h3>
            {isConnecting ? (
              <div className="flex items-center gap-1.5 text-sm text-[var(--jarvis-info-text)]">
                <div className="animate-spin rounded-full h-3 w-3 border-b-2 border-[color:var(--jarvis-border)]" />
                Connecting
              </div>
            ) : isAuthenticated ? (
              <div className="flex items-center gap-1.5 text-sm text-[var(--jarvis-success-text)]">
                <div className="w-2.5 h-2.5 rounded-full bg-[var(--jarvis-success)] shadow-lg shadow-[var(--jarvis-success)]/30" />
                Authenticated
              </div>
            ) : (
              <span className="flex items-center gap-1 px-2 py-0.5 bg-[var(--jarvis-warning-soft)] text-[var(--jarvis-warning-text)] dark:text-[var(--jarvis-warning-text)] rounded-full text-xs font-medium">
                <KeyIcon className="h-3 w-3 dark:text-[var(--jarvis-warning-text)]" />
                OAuth
              </span>
            )}
          </div>
          <IconButton
            ariaLabel="Close"
            tooltip="Close"
            onClick={onClose}
            size="card"
            className="text-[var(--jarvis-subtle)] hover:text-[var(--jarvis-icon)] border-none bg-transparent hover:bg-transparent shadow-none"
          >
            <XMarkIcon className="h-6 w-6" />
          </IconButton>
        </div>

        <div className="flex gap-2">
          {isConnecting && (
            <button
              className="px-3 h-10 border-0 text-[var(--jarvis-text)] text-[var(--jarvis-text)] bg-[var(--jarvis-card-muted)] bg-[var(--jarvis-card-muted)] hover:bg-[var(--jarvis-card-muted)] hover:bg-[var(--jarvis-card-muted)] disabled:bg-[var(--jarvis-bg)]0 text-sm rounded-lg cursor-pointer flex items-center justify-center gap-2"
              disabled={loading}
              onClick={onCancel}
            >
              Cancel
            </button>
          )}
          {isAuthenticated && (
            <button
              className="px-3 h-10 border-0 text-[var(--jarvis-text)] text-[var(--jarvis-text)] bg-[var(--jarvis-card-muted)] bg-[var(--jarvis-card-muted)] hover:bg-[var(--jarvis-card-muted)] hover:bg-[var(--jarvis-card-muted)] disabled:bg-[var(--jarvis-bg)]0 text-sm rounded-lg cursor-pointer flex items-center justify-center gap-2"
              disabled={loading}
              onClick={onClickRevoke}
            >
              {loading ? (
                <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-[color:var(--jarvis-border)]" />
              ) : (
                <TrashIcon className="h-4 w-4" />
              )}
              Revoke
            </button>
          )}
          {!isConnecting && (
            <button
              className="btn-primary flex-1 h-10 text-white font-medium rounded-lg border-0 cursor-pointer flex items-center justify-center gap-2 text-sm transition-colors"
              disabled={loading}
              onClick={handleAuth}
            >
              {loading && <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-[color:var(--jarvis-border)]" />}
              {!(loading || isAuthenticated) && <ArrowPathIcon className="h-4 w-4" />}
              {isAuthenticated ? 'Reconnect' : 'Authenticate'}
            </button>
          )}
        </div>
      </div>
    </Dialog>
  );
};

export default ServerAuthorizationModal;
