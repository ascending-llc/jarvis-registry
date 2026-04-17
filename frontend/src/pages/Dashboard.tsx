import { ArrowPathIcon, MagnifyingGlassIcon, PlusIcon, XMarkIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { HiCommandLine, HiServerStack } from 'react-icons/hi2';
import { useNavigate } from 'react-router-dom';
import McpIcon from '@/assets/McpIcon';
import AgentCard from '@/components/AgentCard';
import FederationCard from '@/components/FederationCard';
import SemanticSearchResults from '@/components/SemanticSearchResults';
import ServerCard from '@/components/ServerCard';
import { useServer } from '@/contexts/ServerContext';
import { useSemanticSearch } from '@/hooks/useSemanticSearch';

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
        <div className='mb-8'>
          <h2 className='text-xl font-bold text-gray-900 dark:text-white mb-4'>MCP Servers</h2>
          <div className='relative'>
            {serverLoading && (
              <div className='absolute inset-0 bg-white/20 dark:bg-black/20 backdrop-blur-sm rounded-2xl flex items-center justify-center z-10'>
                <div className='animate-spin rounded-full h-8 w-8 border-b-2 border-cyan-600'></div>
              </div>
            )}
            {filteredServers.length === 0 ? (
              <div className='text-center py-12 bg-gray-50 dark:bg-gray-800 rounded-lg'>
                <div className='text-gray-400 text-lg mb-2'>No servers found</div>
                <p className='text-gray-500 dark:text-gray-300 text-sm'>
                  {searchTerm || activeFilter !== 'all'
                    ? 'Press Enter in the search bar to search semantically'
                    : 'No servers are registered yet'}
                </p>
                {!searchTerm && activeFilter === 'all' && (
                  <button
                    onClick={handleRegister}
                    className='mt-4 inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-lg text-white bg-blue-600 hover:bg-blue-700 transition-colors'
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
          <h2 className='text-xl font-bold text-gray-900 dark:text-white mb-4'>A2A Agents</h2>
          <div className='relative'>
            {agentLoading && (
              <div className='absolute inset-0 bg-white/20 dark:bg-black/20 backdrop-blur-sm rounded-2xl flex items-center justify-center z-10'>
                <div className='animate-spin rounded-full h-8 w-8 border-b-2 border-cyan-600'></div>
              </div>
            )}
            {filteredAgents.length === 0 ? (
              <div className='text-center py-12 bg-cyan-50 dark:bg-cyan-900/20 rounded-lg border border-cyan-200 dark:border-cyan-800'>
                <div className='text-gray-400 text-lg mb-2'>No agents found</div>
                <p className='text-gray-500 dark:text-gray-300 text-sm'>
                  {searchTerm || activeFilter !== 'all'
                    ? 'Press Enter in the search bar to search semantically'
                    : 'No agents are registered yet'}
                </p>
                {!searchTerm && activeFilter === 'all' && (
                  <button
                    onClick={handleRegister}
                    className='mt-4 inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-lg text-white bg-cyan-600 hover:bg-cyan-700 transition-colors'
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

      {/* External Providers Section */}
      {viewMode === 'external' && (
        <div className='mb-8'>
          <div className='flex items-center justify-between mb-4'>
            <h2 className='text-xl font-bold text-gray-900 dark:text-white'>External Providers</h2>
          </div>
          <div className='relative'>
            {federationsLoading && (
              <div className='absolute inset-0 bg-white/20 dark:bg-black/20 backdrop-blur-sm rounded-2xl flex items-center justify-center z-10'>
                <div className='animate-spin rounded-full h-8 w-8 border-b-2 border-green-600'></div>
              </div>
            )}

            {filteredFederations.length === 0 ? (
              <div className='text-center py-12 bg-gray-50 dark:bg-gray-800 rounded-lg border border-dashed border-gray-300 dark:border-gray-600'>
                <div className='text-gray-400 text-lg mb-2'>
                  {federations.length === 0 ? 'No External Providers Available' : 'No Results Found'}
                </div>
                <p className='text-gray-500 dark:text-gray-300 text-sm max-w-md mx-auto'>
                  {federations.length === 0
                    ? 'Connect an external provider like AWS AgentCore to automatically sync MCP servers and agents.'
                    : 'Try adjusting your search terms.'}
                </p>
                {federations.length === 0 && (
                  <button onClick={handleRegister} className='mt-4 btn-primary inline-flex items-center space-x-2'>
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
    <div className='flex flex-col h-full'>
      {/* Fixed Header Section */}
      <div className='flex-shrink-0 space-y-4 pb-4'>
        {/* View Filter Tabs */}
        <div className='flex gap-2 border-b border-gray-200 dark:border-gray-700 overflow-x-auto'>
          <button
            onClick={() => handleChangeViewFilter('servers')}
            className={`px-4 py-2 text-sm font-medium whitespace-nowrap transition-colors border-b-2 ${
              viewMode === 'servers'
                ? 'border-blue-500 text-blue-600 dark:text-blue-400'
                : 'border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200'
            }`}
          >
            <McpIcon className='h-6 w-6 inline mr-2' />
            MCP Servers
          </button>
          <button
            onClick={() => handleChangeViewFilter('agents')}
            className={`px-4 py-2 text-sm font-medium whitespace-nowrap transition-colors border-b-2 ${
              viewMode === 'agents'
                ? 'border-cyan-500 text-cyan-600 dark:text-cyan-400'
                : 'border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200'
            }`}
          >
            <HiCommandLine className='h-6 w-6 inline mr-2' />
            A2A Agents
          </button>
          <button
            onClick={() => handleChangeViewFilter('external')}
            className={`px-4 py-2 text-sm font-medium whitespace-nowrap transition-colors border-b-2 ${
              viewMode === 'external'
                ? 'border-green-500 text-green-600 dark:text-green-400'
                : 'border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200'
            }`}
          >
            <HiServerStack className='h-6 w-6 inline mr-2' />
            External Providers
          </button>
        </div>

        {/* Search Bar and Refresh Button */}
        <div className='flex gap-4 items-center'>
          <div className='relative flex-1'>
            <div className='absolute inset-y-0 left-0 flex items-center pl-3 pointer-events-none'>
              <MagnifyingGlassIcon className='h-5 w-5 text-gray-400' />
            </div>
            <input
              type='text'
              placeholder='Search servers, agents, descriptions, or tags… (Press Enter to run semantic search; typing filters locally.)'
              className='input pl-10 w-full'
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
                className='absolute inset-y-0 right-0 flex items-center pr-3 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200'
              >
                <XMarkIcon className='h-4 w-4' />
              </button>
            )}
          </div>

          <button
            onClick={handleRegister}
            className='btn-primary flex items-center justify-center space-x-2 flex-shrink-0 w-[250px]'
          >
            <PlusIcon className='h-4 w-4' />
            <span>
              {viewMode === 'agents'
                ? 'Register Agent'
                : viewMode === 'external'
                  ? 'Register External'
                  : 'Register Server'}
            </span>
          </button>

          <button
            onClick={handleRefreshHealth}
            disabled={refreshing}
            className='btn-secondary flex items-center space-x-2 flex-shrink-0'
          >
            <ArrowPathIcon className={`h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} />
            <span>Refresh</span>
          </button>
        </div>

        {/* Results count */}
        <div className='flex items-center justify-between'>
          <p className='text-sm text-gray-500 dark:text-gray-300'>{getCardNumber()}</p>
          <p className='text-xs text-gray-400 dark:text-gray-500'>
            Press Enter to run semantic search; typing filters locally.
          </p>
        </div>
      </div>

      {/* Scrollable Content Area */}
      <div className='flex-1 overflow-y-auto min-h-0 space-y-10 pr-4 sm:pr-6 lg:pr-8 -mr-4 sm:-mr-6 lg:-mr-8'>
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
              <div className='border-t border-gray-200 dark:border-gray-700 pt-6'>
                <div className='flex items-center justify-between mb-4'>
                  <h4 className='text-base font-semibold text-gray-900 dark:text-gray-200'>Keyword search fallback</h4>
                  {semanticError && (
                    <span className='text-xs font-medium text-red-500'>
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
