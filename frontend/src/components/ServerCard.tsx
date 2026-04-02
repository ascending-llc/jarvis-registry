import {
  ArrowPathIcon,
  CheckCircleIcon,
  ClockIcon,
  CogIcon,
  KeyIcon,
  PencilIcon,
  WrenchScrewdriverIcon,
  XMarkIcon,
} from '@heroicons/react/24/outline';
import type React from 'react';
import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useGlobal } from '@/contexts/GlobalContext';
import type { ServerInfo } from '@/contexts/ServerContext';
import { useServer } from '@/contexts/ServerContext';
import SERVICES from '@/services';
import { ServerConnection } from '@/services/mcp/type';
import type { Tool } from '@/services/server/type';
import UTILS from '@/utils';
import ServerAuthorizationModal from './ServerAuthorizationModal';
import ServerConfigModal from './ServerConfigModal';

interface ServerCardProps {
  server: ServerInfo;
}

const ServerCard: React.FC<ServerCardProps> = ({ server }) => {
  const navigate = useNavigate();
  const { showToast } = useGlobal();
  const { cancelPolling, refreshServerData, handleServerUpdate } = useServer();
  const [loading, setLoading] = useState(false);
  const [tools, setTools] = useState<Tool[]>([]);
  const [loadingTools, setLoadingTools] = useState(false);
  const [showTools, setShowTools] = useState(false);
  const [showConfig, setShowConfig] = useState(false);
  const [loadingRefresh, setLoadingRefresh] = useState(false);
  const [showApiKeyDialog, setShowApiKeyDialog] = useState(false);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && showTools) {
        setShowTools(false);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [showTools]);

  const { connectionState, requiresOauth } = server || {};
  const canEdit = !!server?.permissions?.EDIT;

  const getAuthStatusIcon = useCallback(() => {
    if (!requiresOauth) return null;
    if (connectionState === ServerConnection.CONNECTED) {
      return <CheckCircleIcon className='h-4 w-4 text-green-500' />;
    }
    if (connectionState === ServerConnection.DISCONNECTED || connectionState === ServerConnection.ERROR) {
      return <KeyIcon className='h-4 w-4 text-amber-500' />;
    }
    if (connectionState === ServerConnection.CONNECTING) {
      return (
        <>
          <div className='group-hover/auth:hidden animate-spin rounded-full h-3 w-3 border-b-2 border-slate-200' />
          <XMarkIcon className='hidden group-hover/auth:block h-4 w-4 text-red-500' />
        </>
      );
    }
  }, [requiresOauth, connectionState]);

  const toEditPage = (server: ServerInfo) => {
    navigate(`/server-edit?id=${server.id}`);
  };

  const onOpenAuthDialog = () => setShowApiKeyDialog(true);
  const onCloseAuthDialog = () => setShowApiKeyDialog(false);

  const handleCancelAuth = async () => {
    try {
      const result = await SERVICES.MCP.cancelAuth(server.id);
      if (result.success) {
        showToast?.(result?.message || 'OAuth flow cancelled', 'success');
        refreshServerData();
      } else {
        showToast?.(result?.message || 'Unknown error', 'error');
      }
    } catch (_error) {
      showToast?.('Unknown error', 'error');
    }
  };

  const handleAuth = async () => {
    if (connectionState === ServerConnection.CONNECTING) {
      handleCancelAuth();
      cancelPolling?.(server.id);
    } else {
      onOpenAuthDialog();
    }
  };

  const handleViewTools = useCallback(async () => {
    if (loadingTools) return;

    setLoadingTools(true);
    try {
      const result = await SERVICES.SERVER.getServerTools(server.id);
      if (result.toolFunctions) {
        const list: any = [];
        Object.keys(result.toolFunctions).forEach(key => {
          list.push(result.toolFunctions[key]);
        });
        setTools(list);
        setShowTools(true);
      }
    } catch (error) {
      console.error('Failed to fetch tools:', error);
      if (showToast) {
        showToast('Failed to fetch tools', 'error');
      }
    } finally {
      setLoadingTools(false);
    }
  }, [server.id, loadingTools, showToast]);

  const handleRefreshHealth = useCallback(async () => {
    if (loadingRefresh) return;

    setLoadingRefresh(true);
    try {
      const result = await SERVICES.SERVER.refreshServerHealth(server.id);

      if (handleServerUpdate && result) {
        const updates: Partial<ServerInfo> = {
          status: result.status,
          lastCheckedTime: result.lastConnected,
          numTools: result.numTools,
        };
        handleServerUpdate(server.id, updates);
      } else if (refreshServerData) {
        refreshServerData();
      }

      if (showToast) {
        showToast('Health status refreshed successfully', 'success');
      }
    } catch (error: any) {
      if (showToast) {
        showToast(error?.detail?.message || 'Failed to refresh health status', 'error');
      }
    } finally {
      setLoadingRefresh(false);
    }
  }, [server.path, loadingRefresh, refreshServerData, showToast, handleServerUpdate]);

  const handleToggleServer = async (id: string, enabled: boolean) => {
    try {
      setLoading(true);
      await SERVICES.SERVER.refreshServerHealth(id);
      await SERVICES.SERVER.toggleServerStatus(id, { enabled });
      handleServerUpdate(id, { enabled });
      showToast(`Server ${enabled ? 'enabled' : 'disabled'} successfully!`, 'success');
    } catch (error: any) {
      const errorMessage = error.detail?.message || (typeof error.detail === 'string' ? error.detail : '');
      showToast(errorMessage || 'Failed to toggle server', 'error');
    } finally {
      setLoading(false);
    }
  };

  // Generate MCP configuration for the server
  // Check if this is an Anthropic registry server
  const isAnthropicServer = server.tags?.includes('anthropic-registry');

  // Check if this server has security pending
  const isSecurityPending = server.tags?.includes('security-pending');

  return (
    <>
      <div
        className={`group rounded-2xl shadow-sm hover:shadow-xl transition-all duration-300 h-full flex flex-col relative ${
          isAnthropicServer
            ? 'bg-gradient-to-br from-purple-50 to-indigo-50 dark:from-purple-900/20 dark:to-indigo-900/20 border-2 border-purple-200 dark:border-purple-700 hover:border-purple-300 dark:hover:border-purple-600'
            : 'bg-white dark:bg-gray-800 border border-gray-100 dark:border-gray-700 hover:border-gray-200 dark:hover:border-gray-600'
        }`}
      >
        {loading && (
          <div className='absolute inset-0 bg-white/20 dark:bg-black/20 backdrop-blur-sm rounded-2xl flex items-center justify-center z-10'>
            <div className='animate-spin rounded-full h-8 w-8 border-b-2 border-cyan-600'></div>
          </div>
        )}

        <div className='p-4 pb-3'>
          {/* Header */}
          <div className='flex items-start justify-between mb-3 gap-2'>
            <div className='flex-1 min-w-0'>
              <div className='flex items-center gap-1.5 mb-2 min-w-0'>
                {server.permissions?.VIEW ? (
                  <h3
                    className='text-base font-bold text-gray-900 dark:text-white truncate min-w-0 flex-1 cursor-pointer hover:text-purple-600 dark:hover:text-purple-400 transition-colors'
                    onClick={() => navigate(`/server-edit?id=${server.id}&isReadOnly=true`)}
                  >
                    {server.title}
                  </h3>
                ) : (
                  <h3 className='text-base font-bold text-gray-900 dark:text-white truncate min-w-0 flex-1'>
                    {server.title}
                  </h3>
                )}
              </div>

              <div className='flex flex-wrap items-center gap-1.5 mb-2'>
                {server.official && (
                  <span className='px-1.5 py-0.5 text-xs font-semibold bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-300 rounded-full flex-shrink-0 whitespace-nowrap'>
                    OFFICIAL
                  </span>
                )}
                {isAnthropicServer && (
                  <span className='px-1.5 py-0.5 text-xs font-semibold bg-gradient-to-r from-purple-100 to-indigo-100 text-purple-700 dark:from-purple-900/30 dark:to-indigo-900/30 dark:text-purple-300 rounded-full flex-shrink-0 border border-purple-200 dark:border-purple-600 whitespace-nowrap'>
                    ANTHROPIC
                  </span>
                )}
                {/* Check if this is an ASOR server */}
                {server.tags?.includes('asor') && (
                  <span className='px-1.5 py-0.5 text-xs font-semibold bg-gradient-to-r from-orange-100 to-red-100 text-orange-700 dark:from-orange-900/30 dark:to-red-900/30 dark:text-orange-300 rounded-full flex-shrink-0 border border-orange-200 dark:border-orange-600 whitespace-nowrap'>
                    ASOR
                  </span>
                )}
                {isSecurityPending && (
                  <span className='px-1.5 py-0.5 text-xs font-semibold bg-gradient-to-r from-amber-100 to-orange-100 text-amber-700 dark:from-amber-900/30 dark:to-orange-900/30 dark:text-amber-300 rounded-full flex-shrink-0 border border-amber-200 dark:border-amber-600 whitespace-nowrap'>
                    SECURITY PENDING
                  </span>
                )}
              </div>

              <code className='text-xs text-gray-600 dark:text-gray-300 bg-gray-50 dark:bg-gray-800/50 px-1.5 py-0.5 rounded font-mono truncate block max-w-full'>
                {server.path}
              </code>
            </div>

            <div className='flex gap-1 flex-shrink-0'>
              {requiresOauth && (
                <button
                  className='group/auth p-1.5 text-amber-500 hover:bg-gray-50 dark:hover:bg-gray-700/50 rounded-lg transition-all duration-200 flex-shrink-0'
                  onClick={handleAuth}
                  title='Manage API keys'
                >
                  {getAuthStatusIcon()}
                </button>
              )}
              {server?.permissions?.EDIT && (
                <button
                  className='p-1.5 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700/50 rounded-lg transition-all duration-200 flex-shrink-0'
                  onClick={() => toEditPage?.(server)}
                  title='Edit server'
                >
                  <PencilIcon className='h-3.5 w-3.5' />
                </button>
              )}

              {/* Configuration Generator Button */}
              <button
                onClick={() => setShowConfig(true)}
                className='p-1.5 text-gray-400 hover:text-green-600 dark:hover:text-green-300 hover:bg-green-50 dark:hover:bg-green-700/50 rounded-lg transition-all duration-200 flex-shrink-0'
                title='Copy mcp.json configuration'
              >
                <CogIcon className='h-3.5 w-3.5' />
              </button>
            </div>
          </div>
          {/* content */}
          {/* Description */}
          <p className='text-gray-600 dark:text-gray-300 text-xs leading-relaxed line-clamp-2 mb-3'>
            {server.description || 'No description available'}
          </p>

          {/* Tags */}
          {server.tags && server.tags.length > 0 && (
            <div className='flex flex-wrap gap-1 mb-3 max-h-10 overflow-hidden'>
              {server.tags.slice(0, 3).map(tag => (
                <span
                  key={tag}
                  className='px-1.5 py-0.5 text-xs font-medium bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 rounded truncate max-w-[100px]'
                >
                  #{tag}
                </span>
              ))}
              {server.tags.length > 3 && (
                <span className='px-1.5 py-0.5 text-xs font-medium bg-gray-50 dark:bg-gray-800 text-gray-600 dark:text-gray-300 rounded'>
                  +{server.tags.length - 3}
                </span>
              )}
            </div>
          )}
        </div>

        {/* Tools */}
        <div className='px-4 pb-3'>
          <div className='grid grid-cols-2 gap-2'>
            <div className='flex items-center gap-1.5'>
              {(server.numTools || 0) > 0 ? (
                <button
                  onClick={handleViewTools}
                  disabled={loadingTools}
                  className='flex items-center gap-1.5 text-blue-600 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300 disabled:opacity-50 hover:bg-blue-50 dark:hover:bg-blue-900/20 px-1.5 py-0.5 -mx-1.5 -my-0.5 rounded transition-all text-xs'
                  title='View tools'
                >
                  <div className='p-1 bg-blue-50 dark:bg-blue-900/30 rounded'>
                    <WrenchScrewdriverIcon className='h-3.5 w-3.5' />
                  </div>
                  <div>
                    <div className='text-xs font-semibold'>{server.numTools}</div>
                    <div className='text-xs'>Tools</div>
                  </div>
                </button>
              ) : (
                <div className='flex items-center gap-1.5 text-gray-400 dark:text-gray-500'>
                  <div className='p-1 bg-gray-50 dark:bg-gray-800 rounded'>
                    <WrenchScrewdriverIcon className='h-3.5 w-3.5' />
                  </div>
                  <div>
                    <div className='text-xs font-semibold'>{server.numTools || 0}</div>
                    <div className='text-xs'>Tools</div>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className='mt-auto px-3 py-3 border-t border-gray-100 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-900/30 rounded-b-2xl'>
          <div className='flex flex-col sm:flex-row items-center justify-between gap-1'>
            <div className='flex items-center gap-2 flex-wrap justify-center'>
              {/* Status Indicators */}
              <div className='flex items-center gap-1'>
                <div
                  className={`w-2.5 h-2.5 rounded-full ${server.enabled ? 'bg-green-400 shadow-lg shadow-green-400/30' : 'bg-gray-300 dark:bg-gray-600'}`}
                />
                <span className='text-xs font-medium text-gray-700 dark:text-gray-300'>
                  {server.enabled ? 'Enabled' : 'Disabled'}
                </span>
              </div>

              <div className='w-px h-3 bg-gray-200 dark:bg-gray-600' />

              <div className='flex items-center gap-1'>
                <div
                  className={`w-2.5 h-2.5 rounded-full ${
                    server.status === 'active'
                      ? 'bg-emerald-400 shadow-lg shadow-emerald-400/30'
                      : server.status === 'inactive'
                        ? 'bg-orange-400 shadow-lg shadow-orange-400/30'
                        : server.status === 'error'
                          ? 'bg-red-400 shadow-lg shadow-red-400/30'
                          : 'bg-amber-400 shadow-lg shadow-amber-400/30'
                  }`}
                />
                <span className='text-xs font-medium text-gray-700 dark:text-gray-300 max-w-[80px] truncate'>
                  {server.status === 'active'
                    ? 'Active'
                    : server.status === 'inactive'
                      ? 'Inactive'
                      : server.status === 'error'
                        ? 'Error'
                        : 'Unknown'}
                </span>
              </div>
            </div>

            {/* Controls */}
            <div className='flex items-center gap-2'>
              {/* Last Checked */}
              {(() => {
                const timeText = UTILS.formatTimeSince(server.lastCheckedTime);
                return server.lastCheckedTime && timeText ? (
                  <div className='text-xs text-gray-500 dark:text-gray-300 flex items-center gap-1 hidden md:flex'>
                    <ClockIcon className='h-3 w-3' />
                    <span>{timeText}</span>
                  </div>
                ) : null;
              })()}

              {/* Refresh Button */}
              <button
                onClick={handleRefreshHealth}
                disabled={loadingRefresh}
                className='text-gray-500 hover:text-blue-600 dark:hover:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded-lg transition-all duration-200 disabled:opacity-50'
                title='Refresh health status'
              >
                <ArrowPathIcon className={`h-3 w-3 ${loadingRefresh ? 'animate-spin' : ''}`} />
              </button>

              {/* Toggle Switch */}
              <label
                className={`relative inline-flex items-center ${canEdit ? 'cursor-pointer' : 'cursor-not-allowed opacity-60'}`}
                title={canEdit ? 'Toggle server status' : 'No edit permission'}
              >
                <input
                  type='checkbox'
                  checked={server.enabled}
                  onChange={e => handleToggleServer(server.id, e.target.checked)}
                  disabled={!canEdit || loading}
                  className='sr-only peer'
                />
                <div
                  className={`relative w-7 h-4 rounded-full transition-colors duration-200 ease-in-out ${
                    server.enabled ? 'bg-blue-600' : 'bg-gray-300 dark:bg-gray-600'
                  }`}
                >
                  <div
                    className={`absolute top-0.5 left-0 w-3 h-3 bg-white rounded-full transition-transform duration-200 ease-in-out ${
                      server.enabled ? 'translate-x-4' : 'translate-x-0'
                    }`}
                  />
                </div>
              </label>
            </div>
          </div>
        </div>
      </div>

      {/* Tools Modal */}
      {showTools && (
        <div className='fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-50'>
          <div className='bg-white dark:bg-gray-800 rounded-xl p-6 pt-0 max-w-2xl w-full mx-4 max-h-[80vh] overflow-auto'>
            <div className='flex items-center justify-between mb-4 sticky top-0 bg-white dark:bg-gray-800 z-10 pb-2 border-b border-gray-100 dark:border-gray-700 -mx-6 px-6 -mt-6 pt-6'>
              <h3 className='text-lg font-semibold text-gray-900 dark:text-white'>Tools for {server.name}</h3>
              <button
                onClick={() => setShowTools(false)}
                className='text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200'
              >
                <XMarkIcon className='h-6 w-6' />
              </button>
            </div>

            <div className='space-y-4 mt-[2.8rem]'>
              {tools?.length > 0 ? (
                tools.map((tool: Tool, index: number) => (
                  <div key={index} className='border border-gray-200 dark:border-gray-700 rounded-lg p-4'>
                    <h4 className='font-medium text-gray-900 dark:text-white mb-2'>{tool?.function?.name}</h4>
                    {tool?.function?.description && (
                      <p className='text-sm text-gray-600 dark:text-gray-300 mb-2'>{tool?.function?.description}</p>
                    )}
                    {tool?.function?.parameters && (
                      <details className='text-xs'>
                        <summary className='cursor-pointer text-gray-500 dark:text-gray-300'>View Schema</summary>
                        <pre className='mt-2 p-3 bg-gray-50 dark:bg-gray-900 border dark:border-gray-700 rounded overflow-x-auto text-gray-900 dark:text-gray-100'>
                          {JSON.stringify(tool?.function?.parameters, null, 2)}
                        </pre>
                      </details>
                    )}
                  </div>
                ))
              ) : (
                <p className='text-gray-500 dark:text-gray-300'>No tools available for this server.</p>
              )}
            </div>
          </div>
        </div>
      )}

      {showConfig && <ServerConfigModal server={server} isOpen={showConfig} onClose={() => setShowConfig(false)} />}

      {showApiKeyDialog && (
        <ServerAuthorizationModal
          name={server.name}
          serverId={server.id}
          status={connectionState}
          showApiKeyDialog={showApiKeyDialog}
          handleCancelAuth={handleCancelAuth}
          onCloseAuthDialog={onCloseAuthDialog}
        />
      )}
    </>
  );
};

export default ServerCard;
