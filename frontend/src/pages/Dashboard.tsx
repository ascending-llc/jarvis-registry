import { PlusIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import AgentCard from '@/components/AgentCard';
import FederationCard from '@/components/FederationCard';
import IconButton from '@/components/IconButton';
import SemanticSearchResults from '@/components/SemanticSearchResults';
import ServerCard from '@/components/ServerCard';
import { useServer } from '@/contexts/ServerContext';
import { useSemanticSearch } from '@/hooks/useSemanticSearch';

const RefreshGlyph: React.FC<{ className?: string }> = ({ className = '' }) => (
  <svg viewBox='0 0 24 24' fill='none' stroke='currentColor' strokeWidth='1.8' className={className} aria-hidden='true'>
    <polyline points='23 4 23 10 17 10' />
    <path d='M20.49 15a9 9 0 11-2.12-9.36L23 10' />
  </svg>
);

const Dashboard: React.FC = () => {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const {
    viewMode,
    setViewMode,
    activeFilter,

    servers,
    serverLoading,
    refreshServerData,

    agents,
    agentLoading,
    refreshAgentData,

    federations,
    federationsLoading,
    refreshFederationData,
    searchTerm,
    committedQuery,
    setCommittedQuery,
  } = useServer();

  const [refreshing, setRefreshing] = useState(false);

  // Sync viewMode with URL tab parameter
  const urlTab = searchParams.get('tab');
  useEffect(() => {
    if (urlTab === 'agents' || urlTab === 'external' || urlTab === 'workflow') {
      setViewMode(urlTab);
    } else {
      setViewMode('servers');
    }
  }, [urlTab, setViewMode]);

  // Semantic search
  const semanticEnabled = committedQuery.trim().length >= 2;
  const {
    results: semanticResults,
    loading: semanticLoading,
    error: semanticError,
  } = useSemanticSearch(committedQuery, {
    minLength: 2,
    maxResults: 12,
    enabled: semanticEnabled,
  });

  const semanticServers = semanticResults?.servers ?? [];
  const semanticTools = semanticResults?.tools ?? [];
  const semanticAgents = semanticResults?.agents ?? [];
  const semanticDisplayQuery = semanticResults?.query || committedQuery || searchTerm;
  const semanticSectionVisible = semanticEnabled;
  const shouldShowFallbackGrid =
    semanticSectionVisible &&
    (Boolean(semanticError) ||
      (!semanticLoading && semanticServers.length === 0 && semanticTools.length === 0 && semanticAgents.length === 0));

  // Filter servers based on activeFilter and searchTerm
  const filteredServers = useMemo(() => {
    let filtered = servers;

    // Apply filter first
    if (activeFilter === 'enabled') filtered = filtered.filter(s => s.enabled);
    else if (activeFilter === 'disabled') filtered = filtered.filter(s => !s.enabled);
    else if (activeFilter === 'unhealthy')
      filtered = filtered.filter(s => s.status === 'inactive' || s.status === 'error');

    // Then apply search
    if (searchTerm) {
      const query = searchTerm.toLowerCase();
      filtered = filtered.filter(
        server =>
          server.name.toLowerCase().includes(query) ||
          (server.description || '').toLowerCase().includes(query) ||
          server.path.toLowerCase().includes(query) ||
          (server.tags || []).some(tag => tag.toLowerCase().includes(query)),
      );
    }

    return filtered;
  }, [servers, activeFilter, searchTerm]);

  // Filter agents based on activeFilter and searchTerm
  const filteredAgents = useMemo(() => {
    let filtered = agents;

    // Apply filter first
    if (activeFilter === 'enabled') filtered = filtered.filter(a => a.enabled);
    else if (activeFilter === 'disabled') filtered = filtered.filter(a => !a.enabled);
    else if (activeFilter === 'unhealthy')
      filtered = filtered.filter(a => a.status === 'inactive' || a.status === 'error');

    // Then apply search
    if (searchTerm) {
      const query = searchTerm.toLowerCase();
      filtered = filtered.filter(
        agent =>
          agent.name.toLowerCase().includes(query) ||
          (agent.description || '').toLowerCase().includes(query) ||
          agent.path.toLowerCase().includes(query) ||
          (agent.tags || []).some(tag => tag.toLowerCase().includes(query)),
      );
    }
    return filtered;
  }, [agents, activeFilter, searchTerm]);

  // Filter federations based on activeFilter and searchTerm
  const filteredFederations = useMemo(() => {
    let filtered = federations;

    // Apply filter first
    if (activeFilter === 'enabled') filtered = filtered.filter(f => f.status === 'active');
    else if (activeFilter === 'disabled') filtered = filtered.filter(f => f.status !== 'active');
    else if (activeFilter === 'unhealthy') filtered = filtered.filter(f => f.syncStatus === 'failed');

    // Then apply search
    if (searchTerm) {
      const query = searchTerm.toLowerCase();
      filtered = filtered.filter(
        f =>
          f.displayName.toLowerCase().includes(query) ||
          (f.description || '').toLowerCase().includes(query) ||
          (f.tags || []).some(tag => tag.toLowerCase().includes(query)),
      );
    }
    return filtered;
  }, [federations, activeFilter, searchTerm]);

  useEffect(() => {
    if (searchTerm.trim().length === 0 && committedQuery.length > 0) {
      setCommittedQuery('');
    }
  }, [searchTerm, committedQuery, setCommittedQuery]);

  const handleRefreshHealth = async () => {
    setRefreshing(true);
    try {
      if (viewMode === 'servers') {
        await refreshServerData();
      } else if (viewMode === 'agents') {
        await refreshAgentData();
      } else {
        await refreshFederationData();
      }
    } finally {
      setRefreshing(false);
    }
  };

  const handleRegister = useCallback(() => {
    if (viewMode === 'agents') {
      navigate('/agent-registry');
    } else if (viewMode === 'external') {
      navigate('/federation-registry');
    } else {
      navigate('/server-registry');
    }
  }, [viewMode, navigate]);

  const renderDashboardCollections = () => (
    <>
      {/* MCP Servers Section */}
      {viewMode === 'servers' && (
        <div className='mb-8'>
          <div className='relative'>
            {serverLoading && (
              <div className='absolute inset-0 z-10 flex items-center justify-center rounded-2xl bg-[var(--jarvis-overlay)] backdrop-blur-sm'>
                <div className='h-8 w-8 animate-spin rounded-full border-b-2 border-[var(--jarvis-spinner)]'></div>
              </div>
            )}
            {filteredServers.length === 0 ? (
              <div className='rounded-2xl border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] py-12 text-center'>
                <div className='mb-2 text-lg text-[var(--jarvis-faint)]'>No servers found</div>
                <p className='text-sm text-[var(--jarvis-muted)]'>
                  {searchTerm || activeFilter !== 'all'
                    ? 'Press Enter in the search bar to search semantically'
                    : 'No servers are registered yet'}
                </p>
                {!searchTerm && activeFilter === 'all' && (
                  <button
                    onClick={handleRegister}
                    className='mt-4 inline-flex items-center rounded-lg bg-[var(--jarvis-primary)] px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-[var(--jarvis-primary-hover)]'
                  >
                    <PlusIcon className='h-4 w-4 mr-2' />
                    Register Server
                  </button>
                )}
              </div>
            ) : (
              <div
                className='grid'
                style={{
                  gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))',
                  gap: 'clamp(1.5rem, 1.5rem, 2.5rem)',
                }}
              >
                {filteredServers.map(server => (
                  <ServerCard key={server.id} server={server} />
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* A2A Agents Section */}
      {viewMode === 'agents' && (
        <div className='mb-8'>
          <div className='relative'>
            {agentLoading && (
              <div className='absolute inset-0 z-10 flex items-center justify-center rounded-2xl bg-[var(--jarvis-overlay)] backdrop-blur-sm'>
                <div className='h-8 w-8 animate-spin rounded-full border-b-2 border-[var(--jarvis-spinner)]'></div>
              </div>
            )}
            {filteredAgents.length === 0 ? (
              <div className='rounded-2xl border border-[color:var(--jarvis-info-text)]/25 bg-[var(--jarvis-info-soft)] py-12 text-center'>
                <div className='mb-2 text-lg text-[var(--jarvis-faint)]'>No agents found</div>
                <p className='text-sm text-[var(--jarvis-muted)]'>
                  {searchTerm || activeFilter !== 'all'
                    ? 'Press Enter in the search bar to search semantically'
                    : 'No agents are registered yet'}
                </p>
                {!searchTerm && activeFilter === 'all' && (
                  <button
                    onClick={handleRegister}
                    className='mt-4 inline-flex items-center rounded-lg bg-[var(--jarvis-primary)] px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-[var(--jarvis-primary-hover)]'
                  >
                    <PlusIcon className='h-4 w-4 mr-2' />
                    Register Agent
                  </button>
                )}
              </div>
            ) : (
              <div
                className='grid'
                style={{
                  gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))',
                  gap: 'clamp(1.5rem, 1.5rem, 2.5rem)',
                }}
              >
                {filteredAgents.map(agent => (
                  <AgentCard key={agent.id} agent={agent} />
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Workflow Section (Placeholder) */}
      {viewMode === 'workflow' && (
        <div className='mb-8'>
          <div className='rounded-2xl border border-dashed border-[color:var(--jarvis-border-strong)] bg-[var(--jarvis-card)] py-20 text-center'>
            <div className='mb-2 text-xl font-medium text-[var(--jarvis-faint)]'>Coming soon...</div>
            <p className='mx-auto max-w-md text-sm text-[var(--jarvis-muted)]'>
              The Workflow feature is currently in beta and will be available in a future update.
            </p>
          </div>
        </div>
      )}

      {/* External Providers Section */}
      {viewMode === 'external' && (
        <div className='mb-8'>
          <div className='relative'>
            {federationsLoading && (
              <div className='absolute inset-0 z-10 flex items-center justify-center rounded-2xl bg-[var(--jarvis-overlay)] backdrop-blur-sm'>
                <div className='h-8 w-8 animate-spin rounded-full border-b-2 border-[var(--jarvis-spinner)]'></div>
              </div>
            )}

            {filteredFederations.length === 0 ? (
              <div className='rounded-2xl border border-dashed border-[color:var(--jarvis-border-strong)] bg-[var(--jarvis-card)] py-12 text-center'>
                <div className='mb-2 text-lg text-[var(--jarvis-faint)]'>
                  {federations.length === 0 ? 'No External Providers Available' : 'No Results Found'}
                </div>
                <p className='mx-auto max-w-md text-sm text-[var(--jarvis-muted)]'>
                  {federations.length === 0
                    ? 'Connect an external provider like AWS AgentCore to automatically sync MCP servers and agents.'
                    : 'Try adjusting your search terms.'}
                </p>
                {federations.length === 0 && (
                  <button
                    onClick={handleRegister}
                    className='mt-4 inline-flex items-center space-x-2 rounded-lg bg-[var(--jarvis-primary)] px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-[var(--jarvis-primary-hover)]'
                  >
                    <PlusIcon className='h-4 w-4' />
                    <span>Register External</span>
                  </button>
                )}
              </div>
            ) : (
              <div className='grid grid-cols-1 xl:grid-cols-2 gap-4'>
                {filteredFederations.map(federation => (
                  <FederationCard key={federation.id} federation={federation} />
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );

  return (
    <div className='flex flex-col h-full'>
      {/* Section Header */}
      <div className='flex items-start justify-between flex-shrink-0 mb-6'>
        <div className='flex flex-col space-y-1.5'>
          <div className='flex items-center space-x-3'>
            <h2 className='text-2xl font-bold tracking-tight text-[var(--jarvis-text-strong)]'>
              {viewMode === 'servers' && 'MCP Servers'}
              {viewMode === 'agents' && 'A2A Agents'}
              {viewMode === 'external' && 'External Providers'}
              {viewMode === 'workflow' && 'Workflow'}
            </h2>
          </div>
          <p className='text-sm text-[var(--jarvis-muted)] max-w-2xl'>
            {viewMode === 'servers' &&
              'Model Context Protocol servers federated and discoverable through your Jarvis registry.'}
            {viewMode === 'agents' &&
              'Agent-to-Agent protocol endpoints with auto-discovered .well-known capabilities.'}
            {viewMode === 'external' && 'Federated MCP servers and agents from AWS AgentCore and Azure AI Foundry.'}
            {viewMode === 'workflow' &&
              'The Workflow feature is currently in beta and will be available in a future update.'}
          </p>
        </div>

        {viewMode !== 'workflow' && (
          <div className='flex items-center gap-3'>
            <IconButton
              ariaLabel='Refresh'
              tooltip='Refresh'
              onClick={handleRefreshHealth}
              disabled={refreshing}
              spinning={refreshing}
              className='rounded-lg h-10 w-10 flex items-center justify-center border border-[color:var(--jarvis-border)] bg-[var(--jarvis-surface)] hover:bg-[var(--jarvis-card-muted)] text-[var(--jarvis-text)] transition-colors'
            >
              <RefreshGlyph className='h-4 w-4' />
            </IconButton>

            <IconButton
              ariaLabel='Register'
              tooltip={
                viewMode === 'agents'
                  ? 'Register Agent'
                  : viewMode === 'external'
                    ? 'Register Provider'
                    : 'Register Server'
              }
              onClick={handleRegister}
              variant='solid'
              className='rounded-lg h-10 w-10 flex items-center justify-center bg-[var(--jarvis-primary)] text-white hover:bg-[var(--jarvis-primary-hover)] shadow-sm transition-colors'
            >
              <PlusIcon className='h-5 w-5' />
            </IconButton>
          </div>
        )}
      </div>

      {/* Scrollable Content Area */}
      <div className='flex-1 overflow-y-auto min-h-0 space-y-10 pr-4 sm:pr-6 lg:pr-8 -mr-4 sm:-mr-6 lg:-mr-8 pt-2 pb-4'>
        {semanticSectionVisible ? (
          <>
            <SemanticSearchResults
              query={semanticDisplayQuery}
              loading={semanticLoading}
              error={semanticError}
              servers={semanticServers}
              tools={semanticTools}
              agents={semanticAgents}
            />

            {shouldShowFallbackGrid && (
              <div className='border-t border-[color:var(--jarvis-border)] pt-6'>
                <div className='flex items-center justify-between mb-4'>
                  <h4 className='text-base font-semibold text-[var(--jarvis-text-strong)]'>Keyword search fallback</h4>
                  {semanticError && (
                    <span className='text-xs font-medium text-[var(--jarvis-danger-text)]'>
                      Showing local matches because semantic search is unavailable
                    </span>
                  )}
                </div>
                {renderDashboardCollections()}
              </div>
            )}
          </>
        ) : (
          renderDashboardCollections()
        )}
      </div>
    </div>
  );
};

export default Dashboard;
