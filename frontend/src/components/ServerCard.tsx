import {
  ArrowPathIcon,
  CheckCircleIcon,
  ClockIcon,
  Cog6ToothIcon,
  KeyIcon,
  PencilSquareIcon,
  WrenchScrewdriverIcon,
  XMarkIcon,
} from '@heroicons/react/24/outline';
import type React from 'react';
import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import agentcoreIcon from '@/assets/agentcore.svg';
import IconButton from '@/components/IconButton';
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
  const [activeTooltip, setActiveTooltip] = useState<string | null>(null);

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
      return <CheckCircleIcon className='h-4 w-4 text-[var(--jarvis-success)]' />;
    }
    if (connectionState === ServerConnection.DISCONNECTED || connectionState === ServerConnection.ERROR) {
      return <KeyIcon className='h-3.5 w-3.5 text-[var(--jarvis-primary-text)]' />;
    }
    if (connectionState === ServerConnection.CONNECTING) {
      return (
        <>
          <div className='group-hover/auth:hidden h-3 w-3 animate-spin rounded-full border-b-2 border-[var(--jarvis-text)]' />
          <XMarkIcon className='hidden h-4 w-4 text-[var(--jarvis-danger-text)] group-hover/auth:block' />
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

  const hasAgentCoreTags =
    server.tags?.includes('federated') && server.tags?.includes('aws') && server.tags?.includes('agentcore');

  return (
    <>
      <div
        className={`group rounded-2xl shadow-sm hover:shadow-xl hover:-translate-y-1 transition-all duration-300 h-full flex flex-col relative ${
          isAnthropicServer
            ? 'border border-[color:var(--jarvis-primary-soft)] bg-gradient-to-br from-[#1f2432] to-[#242b3f] hover:border-[var(--jarvis-primary-hover)]'
            : 'border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] hover:border-[color:var(--jarvis-border-strong)]'
        }`}
      >
        {loading && (
          <div className='absolute inset-0 z-10 flex items-center justify-center rounded-2xl bg-[var(--jarvis-overlay)] backdrop-blur-sm'>
            <div className='h-8 w-8 animate-spin rounded-full border-b-2 border-[var(--jarvis-spinner)]'></div>
          </div>
        )}

        <div className='p-4 pb-2.5'>
          {/* Header */}
          <div className='mb-2.5 flex items-start justify-between gap-2'>
            <div className='flex-1 min-w-0'>
              <div className='mb-1.5 flex min-w-0 items-center gap-1.5'>
                {server.permissions?.VIEW ? (
                  <h3
                    className='min-w-0 flex-1 truncate cursor-pointer text-[15px] font-medium text-[var(--jarvis-text)] transition-colors hover:text-[var(--jarvis-text-strong)]'
                    onClick={() => navigate(`/server-edit?id=${server.id}&isReadOnly=true`)}
                  >
                    {server.title}
                  </h3>
                ) : (
                  <h3 className='min-w-0 flex-1 truncate text-[15px] font-medium text-[var(--jarvis-text)]'>
                    {server.title}
                  </h3>
                )}
              </div>

              <div className='mb-2 flex flex-wrap items-center gap-1.5'>
                {server.official && (
                  <span className='whitespace-nowrap rounded-full bg-[var(--jarvis-primary-soft)] px-1.5 py-0.5 text-xs font-semibold text-[var(--jarvis-primary-text-hover)]'>
                    OFFICIAL
                  </span>
                )}
                {isAnthropicServer && (
                  <span className='whitespace-nowrap rounded-full border border-[color:var(--jarvis-primary-soft)] bg-[var(--jarvis-primary-soft)] px-1.5 py-0.5 text-xs font-semibold text-[var(--jarvis-primary-text-hover)]'>
                    ANTHROPIC
                  </span>
                )}
                {/* Check if this is an ASOR server */}
                {server.tags?.includes('asor') && (
                  <span className='whitespace-nowrap rounded-full border border-[var(--jarvis-warning)]/35 bg-[var(--jarvis-warning-soft)] px-1.5 py-0.5 text-xs font-semibold text-[var(--jarvis-warning-text)]'>
                    ASOR
                  </span>
                )}
                {isSecurityPending && (
                  <span className='whitespace-nowrap rounded-full border border-[var(--jarvis-warning)]/35 bg-[var(--jarvis-warning-soft)] px-1.5 py-0.5 text-xs font-semibold text-[var(--jarvis-warning-text)]'>
                    SECURITY PENDING
                  </span>
                )}
              </div>

              <div className='block max-w-full truncate font-mono text-[12px] font-medium text-[color:var(--jarvis-primary)]'>
                {server.path}
              </div>
            </div>

            <div className='flex flex-shrink-0 gap-0.5'>
              {requiresOauth && (
                <IconButton
                  ariaLabel='Manage API keys'
                  tooltip='Auth scopes'
                  onClick={handleAuth}
                  size='card'
                  tooltipVisible={activeTooltip === 'auth'}
                  onMouseEnter={() => setActiveTooltip('auth')}
                  onMouseLeave={() => setActiveTooltip(null)}
                  onFocus={() => setActiveTooltip('auth')}
                  onBlur={() => setActiveTooltip(null)}
                  className='group/auth text-[var(--jarvis-primary-text)] hover:bg-[var(--jarvis-primary-soft)] hover:text-[var(--jarvis-primary-text-hover)]'
                >
                  {getAuthStatusIcon()}
                </IconButton>
              )}
              {server?.permissions?.EDIT && (
                <IconButton
                  ariaLabel='Edit server'
                  tooltip='Edit'
                  onClick={() => toEditPage?.(server)}
                  size='card'
                  tooltipVisible={activeTooltip === 'edit'}
                  onMouseEnter={() => setActiveTooltip('edit')}
                  onMouseLeave={() => setActiveTooltip(null)}
                  onFocus={() => setActiveTooltip('edit')}
                  onBlur={() => setActiveTooltip(null)}
                  className='text-[var(--jarvis-icon)] hover:bg-[var(--jarvis-primary-soft)] hover:text-[var(--jarvis-icon-hover)]'
                >
                  <PencilSquareIcon className='h-3.5 w-3.5' />
                </IconButton>
              )}

              {/* Configuration Generator Button */}
              <IconButton
                ariaLabel='Copy MCP configuration'
                tooltip='Settings'
                onClick={() => setShowConfig(true)}
                size='card'
                tooltipVisible={activeTooltip === 'settings'}
                onMouseEnter={() => setActiveTooltip('settings')}
                onMouseLeave={() => setActiveTooltip(null)}
                onFocus={() => setActiveTooltip('settings')}
                onBlur={() => setActiveTooltip(null)}
                className='text-[var(--jarvis-icon)] hover:bg-[var(--jarvis-primary-soft)] hover:text-[var(--jarvis-icon-hover)]'
              >
                <Cog6ToothIcon className='h-3.5 w-3.5' />
              </IconButton>
            </div>
          </div>
          {/* content */}
          {/* Description */}
          <p className='mb-3 line-clamp-2 text-[12px] leading-[1.45] text-[var(--jarvis-subtle)]'>
            {server.description || 'No description available'}
          </p>

          {/* Tags */}
          {server.tags && server.tags.length > 0 && (
            <div className='flex flex-wrap gap-1 mb-3 max-h-10 overflow-hidden'>
              {server.tags.slice(0, 3).map(tag => (
                <span
                  key={tag}
                  className='max-w-[100px] truncate rounded bg-[var(--jarvis-info-soft)] px-1.5 py-0.5 text-xs font-medium text-[var(--jarvis-info-text)]'
                >
                  #{tag}
                </span>
              ))}
              {server.tags.length > 3 && (
                <span className='rounded bg-[var(--jarvis-card-muted)] px-1.5 py-0.5 text-xs font-medium text-[var(--jarvis-subtle)]'>
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
                  className='-mx-1.5 -my-0.5 flex items-center gap-1.5 rounded px-1.5 py-0.5 text-xs text-[var(--jarvis-info-text)] transition-all hover:bg-[var(--jarvis-info-soft)] hover:text-[var(--jarvis-icon-hover)] disabled:opacity-50'
                  title='View tools'
                >
                  <div className='rounded bg-[var(--jarvis-card-muted)] p-1'>
                    <WrenchScrewdriverIcon className='h-3.5 w-3.5' />
                  </div>
                  <div>
                    <div className='text-xs font-semibold'>{server.numTools}</div>
                    <div className='text-xs'>Tools</div>
                  </div>
                </button>
              ) : (
                <div className='flex items-center gap-1.5 text-[var(--jarvis-faint)]'>
                  <div className='rounded bg-[var(--jarvis-card-muted)] p-1'>
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
        <div className='mt-auto rounded-b-2xl border-t border-[color:var(--jarvis-border)] bg-[var(--jarvis-bg)]/70 px-3 py-3'>
          <div className='flex flex-col sm:flex-row items-center justify-between gap-1'>
            <div className='flex items-center gap-2 flex-wrap justify-center'>
              {/* Status Indicators */}
              <div className='flex items-center gap-1'>
                <div
                  className={`h-2.5 w-2.5 rounded-full ${server.enabled ? 'bg-[var(--jarvis-success)] shadow-lg shadow-emerald-500/30' : 'bg-[var(--jarvis-faint)]'}`}
                />
                <span className='text-xs font-medium text-[var(--jarvis-muted)]'>
                  {server.enabled ? 'Enabled' : 'Disabled'}
                </span>
              </div>

              <div className='h-3 w-px bg-[color:var(--jarvis-border)]' />

              <div className='flex items-center gap-1'>
                <div
                  className={`w-2.5 h-2.5 rounded-full ${
                    server.status === 'active'
                      ? 'bg-[var(--jarvis-success)] shadow-lg shadow-emerald-500/30'
                      : server.status === 'inactive'
                        ? 'bg-[var(--jarvis-warning)] shadow-lg shadow-amber-500/30'
                        : server.status === 'error'
                          ? 'bg-[var(--jarvis-danger)] shadow-lg shadow-red-500/30'
                          : 'bg-[var(--jarvis-warning)] shadow-lg shadow-amber-500/30'
                  }`}
                />
                <span className='max-w-[80px] truncate text-xs font-medium text-[var(--jarvis-muted)]'>
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
                  <div className='hidden items-center gap-1 text-xs text-[var(--jarvis-muted)] md:flex'>
                    <ClockIcon className='h-3 w-3' />
                    <span>{timeText}</span>
                  </div>
                ) : null;
              })()}

              {/* Refresh Button */}
              <IconButton
                ariaLabel='Refresh health status'
                tooltip='Refresh'
                onClick={handleRefreshHealth}
                disabled={loadingRefresh}
                size='card'
                className='text-[var(--jarvis-icon)] transition-all duration-200 hover:bg-[var(--jarvis-primary-soft)] hover:text-[var(--jarvis-icon-hover)]'
              >
                <ArrowPathIcon className={`h-3 w-3 ${loadingRefresh ? 'animate-spin' : ''}`} />
              </IconButton>

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
                    server.enabled ? 'bg-[var(--jarvis-primary)]' : 'bg-[var(--jarvis-faint)]'
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

        {/* AgentCore Icon - Fixed position */}
        {hasAgentCoreTags && (
          <img
            src={agentcoreIcon}
            alt='AWS AgentCore'
            className='absolute bottom-16 right-3 h-6 w-6 rounded-md'
            title='AWS AgentCore'
          />
        )}
      </div>

      {/* Tools Modal */}
      {showTools && (
        <div className='fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm'>
          <div className='max-h-[80vh] w-full max-w-2xl overflow-auto rounded-xl bg-[var(--jarvis-card)] p-6 pt-0 text-[var(--jarvis-text)] shadow-xl'>
            <div className='sticky top-0 z-10 -mx-6 -mt-6 mb-4 flex items-center justify-between border-b border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] px-6 pb-2 pt-6'>
              <h3 className='text-lg font-semibold text-[var(--jarvis-text-strong)]'>Tools for {server.name}</h3>
              <IconButton
                ariaLabel='Close'
                tooltip='Close'
                onClick={() => setShowTools(false)}
                size='card'
                className='text-[var(--jarvis-icon)] transition-colors hover:text-[var(--jarvis-icon-hover)] border-none bg-transparent hover:bg-transparent shadow-none'
              >
                <XMarkIcon className='h-6 w-6' />
              </IconButton>
            </div>

            <div className='space-y-4 mt-[2.8rem]'>
              {tools?.length > 0 ? (
                tools.map((tool: Tool, index: number) => (
                  <div
                    key={index}
                    className='rounded-lg border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card-muted)] p-4'
                  >
                    <h4 className='mb-2 font-medium text-[var(--jarvis-text-strong)]'>{tool?.function?.name}</h4>
                    {tool?.function?.description && (
                      <p className='mb-2 text-sm text-[var(--jarvis-muted)]'>{tool?.function?.description}</p>
                    )}
                    {tool?.function?.parameters && (
                      <details className='text-xs'>
                        <summary className='cursor-pointer text-[var(--jarvis-muted)]'>View Schema</summary>
                        <pre className='mt-2 overflow-x-auto rounded border border-[color:var(--jarvis-border)] bg-[var(--jarvis-surface)] p-3 text-[var(--jarvis-text)]'>
                          {JSON.stringify(tool?.function?.parameters, null, 2)}
                        </pre>
                      </details>
                    )}
                  </div>
                ))
              ) : (
                <p className='text-[var(--jarvis-muted)]'>No tools available for this server.</p>
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
