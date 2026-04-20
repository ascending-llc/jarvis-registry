import { MagnifyingGlassIcon, PlusIcon, XMarkIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { HiCommandLine, HiServerStack } from 'react-icons/hi2';
import { useNavigate } from 'react-router-dom';
import McpIcon from '@/assets/McpIcon';
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
  } = useServer();

  const [searchTerm, setSearchTerm] = useState('');
  const [committedQuery, setCommittedQuery] = useState('');
  const [refreshing, setRefreshing] = useState(false);

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
    else if (activeFilter === 'unhealthy') filtered = filtered.filter(s => s.status === 'inactive');

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

  // Filter federations based on searchTerm
  const filteredFederations = useMemo(() => {
    let filtered = federations;
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
  }, [federations, searchTerm]);

  useEffect(() => {
    if (searchTerm.trim().length === 0 && committedQuery.length > 0) {
      setCommittedQuery('');
    }
  }, [searchTerm, committedQuery]);

  const handleSemanticSearch = useCallback(() => {
    const trimmed = searchTerm.trim();
    setCommittedQuery(trimmed);
  }, [searchTerm]);

  const handleClearSearch = useCallback(() => {
    setSearchTerm('');
    setCommittedQuery('');
  }, []);

  const handleChangeViewFilter = useCallback(
    (filter: 'servers' | 'agents' | 'external') => {
      setViewMode(filter);
      if (semanticSectionVisible) {
        setSearchTerm('');
        setCommittedQuery('');
      }
    },
    [semanticSectionVisible, setViewMode],
  );

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
        <div className="mb-8">
          <div className="relative">
            {serverLoading && (
              <div className="absolute inset-0 z-10 flex items-center justify-center rounded-2xl bg-[var(--jarvis-overlay)] backdrop-blur-sm">
                <div className="h-8 w-8 animate-spin rounded-full border-b-2 border-[var(--jarvis-spinner)]"></div>
              </div>
            )}
            {filteredServers.length === 0 ? (
              <div className="rounded-2xl border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] py-12 text-center">
                <div className="mb-2 text-lg text-[var(--jarvis-faint)]">No servers found</div>
                <p className="text-sm text-[var(--jarvis-muted)]">
                  {searchTerm || activeFilter !== 'all'
                    ? 'Press Enter in the search bar to search semantically'
                    : 'No servers are registered yet'}
                </p>
                {!searchTerm && activeFilter === 'all' && (
                  <button
                    onClick={handleRegister}
                    className="mt-4 inline-flex items-center rounded-lg bg-[var(--jarvis-primary)] px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-[var(--jarvis-primary-hover)]"
                  >
                    <PlusIcon className="h-4 w-4 mr-2" />
                    Register Server
                  </button>
                )}
              </div>
            ) : (
              <div
                className="grid"
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
        <div className="mb-8">
          <div className="relative">
            {agentLoading && (
              <div className="absolute inset-0 z-10 flex items-center justify-center rounded-2xl bg-[var(--jarvis-overlay)] backdrop-blur-sm">
                <div className="h-8 w-8 animate-spin rounded-full border-b-2 border-[var(--jarvis-spinner)]"></div>
              </div>
            )}
            {filteredAgents.length === 0 ? (
              <div className="rounded-2xl border border-[color:var(--jarvis-info-text)]/25 bg-[var(--jarvis-info-soft)] py-12 text-center">
                <div className="mb-2 text-lg text-[var(--jarvis-faint)]">No agents found</div>
                <p className="text-sm text-[var(--jarvis-muted)]">
                  {searchTerm || activeFilter !== 'all'
                    ? 'Press Enter in the search bar to search semantically'
                    : 'No agents are registered yet'}
                </p>
                {!searchTerm && activeFilter === 'all' && (
                  <button
                    onClick={handleRegister}
                    className="mt-4 inline-flex items-center rounded-lg bg-[var(--jarvis-primary)] px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-[var(--jarvis-primary-hover)]"
                  >
                    <PlusIcon className="h-4 w-4 mr-2" />
                    Register Agent
                  </button>
                )}
              </div>
            ) : (
              <div
                className="grid"
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

      {/* External Providers Section */}
      {viewMode === 'external' && (
        <div className="mb-8">
          <div className="relative">
            {federationsLoading && (
              <div className="absolute inset-0 z-10 flex items-center justify-center rounded-2xl bg-[var(--jarvis-overlay)] backdrop-blur-sm">
                <div className="h-8 w-8 animate-spin rounded-full border-b-2 border-[var(--jarvis-spinner)]"></div>
              </div>
            )}

            {filteredFederations.length === 0 ? (
              <div className="rounded-2xl border border-dashed border-[color:var(--jarvis-border-strong)] bg-[var(--jarvis-card)] py-12 text-center">
                <div className="mb-2 text-lg text-[var(--jarvis-faint)]">
                  {federations.length === 0 ? 'No External Providers Available' : 'No Results Found'}
                </div>
                <p className="mx-auto max-w-md text-sm text-[var(--jarvis-muted)]">
                  {federations.length === 0
                    ? 'Connect an external provider like AWS AgentCore to automatically sync MCP servers and agents.'
                    : 'Try adjusting your search terms.'}
                </p>
                {federations.length === 0 && (
                  <button
                    onClick={handleRegister}
                    className="mt-4 inline-flex items-center space-x-2 rounded-lg bg-[var(--jarvis-primary)] px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-[var(--jarvis-primary-hover)]"
                  >
                    <PlusIcon className="h-4 w-4" />
                    <span>Register External</span>
                  </button>
                )}
              </div>
            ) : (
              <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
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

  const getCardNumber = () => {
    const serverLength = semanticSectionVisible ? semanticServers.length : filteredServers?.length || 0;
    const agentLength = semanticSectionVisible ? semanticAgents.length : filteredAgents?.length || 0;
    if (viewMode === 'servers') {
      return `Showing ${serverLength} servers`;
    } else if (viewMode === 'agents') {
      return `Showing ${agentLength} agents`;
    } else if (viewMode === 'external') {
      return `Showing ${filteredFederations.length} providers`;
    }
  };

  return (
    <div className="flex flex-col h-full">
      {/* Fixed Header Section */}
      <div className="flex-shrink-0 space-y-4 mb-6">
        {/* View Filter Tabs */}
        <div className="flex gap-6 overflow-x-auto border-b border-[color:var(--jarvis-border)]">
          <button
            onClick={() => handleChangeViewFilter('servers')}
            className={`inline-flex items-center gap-2 border-b-2 px-1 py-3 text-[15px] font-medium whitespace-nowrap transition-colors ${
 viewMode === 'servers'
 ? 'border-[var(--jarvis-primary)] text-[var(--jarvis-primary)]'
 : 'border-transparent text-[var(--jarvis-muted)] hover:text-[var(--jarvis-text)] hover:border-[color:var(--jarvis-border)]'
 }`}
          >
            MCP Servers
          </button>
          <button
            onClick={() => handleChangeViewFilter('agents')}
            className={`inline-flex items-center gap-2 border-b-2 px-1 py-3 text-[15px] font-medium whitespace-nowrap transition-colors ${
 viewMode === 'agents'
 ? 'border-[var(--jarvis-primary)] text-[var(--jarvis-primary)]'
 : 'border-transparent text-[var(--jarvis-muted)] hover:text-[var(--jarvis-text)] hover:border-[color:var(--jarvis-border)]'
 }`}
          >
            A2A Agents
          </button>
          <button
            onClick={() => handleChangeViewFilter('external')}
            className={`inline-flex items-center gap-2 border-b-2 px-1 py-3 text-[15px] font-medium whitespace-nowrap transition-colors ${
 viewMode === 'external'
 ? 'border-[var(--jarvis-primary)] text-[var(--jarvis-primary)]'
 : 'border-transparent text-[var(--jarvis-muted)] hover:text-[var(--jarvis-text)] hover:border-[color:var(--jarvis-border)]'
 }`}
          >
            External Providers
          </button>
        </div>

        {/* Search Bar and Refresh Button */}
        <div className="flex items-center gap-3">
          <div className="relative flex-1">
            <div className="absolute inset-y-0 left-0 flex items-center pl-3 pointer-events-none">
              <MagnifyingGlassIcon className="h-4 w-4 text-[var(--jarvis-subtle)]" />
            </div>
            <input
              type='text'
              placeholder='Search servers, agents, descriptions, or tags...'
              className="h-10 w-full rounded-lg border border-[color:var(--jarvis-input-border)] bg-[var(--jarvis-input-bg)] pl-10 pr-10 text-sm text-[var(--jarvis-text)] outline-none transition placeholder:text-[var(--jarvis-input-placeholder)] focus:border-[var(--jarvis-primary)] focus:bg-[var(--jarvis-input-bg-focus)] focus:ring-2 focus:ring-[var(--jarvis-primary)]"
              value={searchTerm}
              onChange={e => setSearchTerm(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  handleSemanticSearch();
                }
              }}
            />
            {searchTerm && (
              <button
                onClick={handleClearSearch}
                className="absolute inset-y-0 right-0 flex items-center pr-3 text-[var(--jarvis-subtle)] hover:text-[var(--jarvis-text)]"
              >
                <XMarkIcon className="h-4 w-4" />
              </button>
            )}
          </div>

          <IconButton
            ariaLabel='Register'
            tooltip={
              viewMode === 'agents'
                ? 'Register agent'
                : viewMode === 'external'
                  ? 'Register external provider'
                  : 'Register server'
            }
            onClick={handleRegister}
            variant="solid"
            className="flex-shrink-0"
          >
            <PlusIcon className="h-5 w-5" />
          </IconButton>

          <IconButton
            ariaLabel='Refresh'
            tooltip='Refresh'
            onClick={handleRefreshHealth}
            disabled={refreshing}
            spinning={refreshing}
            className="rounded-lg h-10 w-10 flex items-center justify-center border border-[color:var(--jarvis-border)] bg-[var(--jarvis-surface)] hover:bg-[var(--jarvis-card-muted)] text-[var(--jarvis-text)]"
          >
            <RefreshGlyph className="h-4 w-4" />
          </IconButton>
        </div>
      </div>

      {/* Scrollable Content Area */}
      <div className="flex-1 overflow-y-auto min-h-0 space-y-10 pr-4 sm:pr-6 lg:pr-8 -mr-4 sm:-mr-6 lg:-mr-8">
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
              <div className="border-t border-[color:var(--jarvis-border)] pt-6">
                <div className="flex items-center justify-between mb-4">
                  <h4 className="text-base font-semibold text-[var(--jarvis-text-strong)]">Keyword search fallback</h4>
                  {semanticError && (
                    <span className="text-xs font-medium text-[var(--jarvis-danger-text)]">
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
