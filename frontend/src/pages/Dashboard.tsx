import { ArrowPathIcon, MagnifyingGlassIcon, PlusIcon, XMarkIcon } from '@heroicons/react/24/outline';
import axios from 'axios';
import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { HiCommandLine, HiServerStack } from 'react-icons/hi2';
import { useNavigate } from 'react-router-dom';

import McpIcon from '@/assets/McpIcon';
import AgentCard from '@/components/AgentCard';
import SemanticSearchResults from '@/components/SemanticSearchResults';
import ServerCard from '@/components/ServerCard';
import { useAuth } from '@/contexts/AuthContext';
import { useGlobal } from '@/contexts/GlobalContext';
import type { ServerInfo } from '@/contexts/ServerContext';
import { useServer } from '@/contexts/ServerContext';
import { useSemanticSearch } from '@/hooks/useSemanticSearch';

interface Agent {
  name: string;
  path: string;
  url?: string;
  description?: string;
  version?: string;
  visibility?: 'public' | 'private' | 'group-restricted';
  trust_level?: 'community' | 'verified' | 'trusted' | 'unverified';
  enabled: boolean;
  tags?: string[];
  last_checked_time?: string;
  usersCount?: number;
  rating?: number;
  status?: 'healthy' | 'healthy-auth-expired' | 'unhealthy' | 'unknown';
}

const Dashboard: React.FC = () => {
  const navigate = useNavigate();
  const {
    servers,
    serverLoading,

    agents,
    agentLoading,

    viewMode,
    setViewMode,
    activeFilter,

    refreshServerData,
    refreshAgentData,
    handleServerUpdate,
    setAgents,
  } = useServer();
  const { user } = useAuth();
  const [searchTerm, setSearchTerm] = useState('');
  const [committedQuery, setCommittedQuery] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  const { showToast } = useGlobal();

  // Agent state management
  const [editingAgent, setEditingAgent] = useState<Agent | null>(null);
  const [agentApiToken, setAgentApiToken] = useState<string | null>(null);

  // Local view filter that includes 'external' mode not in context
  const [viewFilter, setViewFilter] = useState<'servers' | 'agents' | 'external'>('servers');
  const [editAgentForm, setEditAgentForm] = useState({
    name: '',
    path: '',
    description: '',
    version: '',
    visibility: 'private' as 'public' | 'private' | 'group-restricted',
    trust_level: 'community' as 'community' | 'verified' | 'trusted' | 'unverified',
    tags: [] as string[],
  });
  const [editAgentLoading, setEditAgentLoading] = useState(false);

  const handleAgentUpdate = useCallback(
    (path: string, updates: Partial<Agent>) => {
      setAgents(prevAgents => prevAgents.map(agent => (agent.path === path ? { ...agent, ...updates } : agent)));
    },
    [setAgents],
  );

  // External registry tags - can be configured via environment or constants
  // Default tags that identify servers from external registries
  const EXTERNAL_REGISTRY_TAGS = ['anthropic-registry', 'workday-asor', 'asor', 'federated'];

  // Separate internal and external registry servers
  const internalServers = useMemo(() => {
    return servers.filter(s => {
      const serverTags = s.tags || [];
      return !EXTERNAL_REGISTRY_TAGS.some(tag => serverTags.includes(tag));
    });
  }, [servers]);

  const externalServers = useMemo(() => {
    return servers.filter(s => {
      const serverTags = s.tags || [];
      return EXTERNAL_REGISTRY_TAGS.some(tag => serverTags.includes(tag));
    });
  }, [servers]);

  // Separate internal and external registry agents
  const internalAgents = useMemo(() => {
    return agents.filter(a => {
      const agentTags = a.tags || [];
      return !EXTERNAL_REGISTRY_TAGS.some(tag => agentTags.includes(tag));
    });
  }, [agents]);

  const externalAgents = useMemo(() => {
    return agents.filter(a => {
      const agentTags = a.tags || [];
      return EXTERNAL_REGISTRY_TAGS.some(tag => agentTags.includes(tag));
    });
  }, [agents]);

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
    let filtered = internalServers;

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
  }, [internalServers, activeFilter, searchTerm]);

  // Filter external servers based on searchTerm
  const filteredExternalServers = useMemo(() => {
    let filtered = externalServers;

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
  }, [externalServers, searchTerm]);

  // Filter external agents based on searchTerm
  const filteredExternalAgents = useMemo(() => {
    let filtered = externalAgents;

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
  }, [externalAgents, searchTerm]);

  // Filter agents based on activeFilter and searchTerm
  const filteredAgents = useMemo(() => {
    let filtered = internalAgents;

    // Apply filter first
    if (activeFilter === 'enabled') filtered = filtered.filter(a => a.enabled);
    else if (activeFilter === 'disabled') filtered = filtered.filter(a => !a.enabled);
    else if (activeFilter === 'unhealthy') filtered = filtered.filter(a => a.status === 'unhealthy');

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
  }, [internalAgents, activeFilter, searchTerm]);

  useEffect(() => {
    if (searchTerm.trim().length === 0 && committedQuery.length > 0) {
      setCommittedQuery('');
    }
  }, [searchTerm, committedQuery]);

  // Sync local viewFilter with context viewMode (but not vice versa for 'external')
  useEffect(() => {
    if (viewMode !== viewFilter && viewFilter !== 'external') {
      setViewFilter(viewMode);
    }
  }, [viewMode, viewFilter]);

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
      setViewFilter(filter);
      // Sync with context viewMode (external is local to Dashboard)
      if (filter !== 'external') {
        setViewMode(filter);
      }
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
      if (viewFilter === 'servers') {
        await refreshServerData();
      } else if (viewFilter === 'agents') {
        await refreshAgentData();
      } else {
        await refreshServerData();
        await refreshAgentData();
      }
    } finally {
      setRefreshing(false);
    }
  };

  const handleEditServer = async (server: ServerInfo) => {
    navigate(`/server-edit?id=${server.id}`);
  };

  const handleEditAgent = async (agent: Agent) => {
    // For now, just populate the form with existing data
    // In the future, we might fetch additional details from an API
    setEditingAgent(agent);
    setEditAgentForm({
      name: agent.name,
      path: agent.path,
      description: agent.description || '',
      version: agent.version || '1.0.0',
      visibility: agent.visibility || 'private',
      trust_level: agent.trust_level || 'community',
      tags: agent.tags || [],
    });
  };

  const handleCloseEdit = () => {
    setEditingAgent(null);
  };

  const handleSaveEditAgent = async () => {
    if (editAgentLoading || !editingAgent) return;

    try {
      setEditAgentLoading(true);
      showToast('Agent editing is not yet implemented', 'error');
    } catch (error: any) {
      showToast(error.response?.data?.detail || 'Failed to update agent', 'error');
    } finally {
      setEditAgentLoading(false);
    }
  };

  const handleToggleAgent = async (path: string, enabled: boolean) => {
    setAgents(prevAgents => prevAgents.map(agent => (agent.path === path ? { ...agent, enabled } : agent)));
    try {
      await axios.post(`/api/agents${path}/toggle?enabled=${enabled}`);
      showToast(`Agent ${enabled ? 'enabled' : 'disabled'} successfully!`, 'success');
    } catch (error: any) {
      setAgents(prevAgents => prevAgents.map(agent => (agent.path === path ? { ...agent, enabled: !enabled } : agent)));
      showToast(error.response?.data?.detail || 'Failed to toggle agent', 'error');
    }
  };

  const handleRegisterServer = useCallback(() => {
    navigate('/server-registry');
  }, []);

  const renderDashboardCollections = () => (
    <>
      {/* MCP Servers Section */}
      {viewFilter === 'servers' && (filteredServers.length > 0 || (!searchTerm && activeFilter === 'all')) && (
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
                    onClick={handleRegisterServer}
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
                  <ServerCard
                    key={server.id}
                    server={server}
                    onEdit={handleEditServer}
                    onServerUpdate={handleServerUpdate}
                    onRefreshSuccess={refreshServerData}
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* A2A Agents Section */}
      {viewFilter === 'agents' && (filteredAgents.length > 0 || (!searchTerm && activeFilter === 'all')) && (
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
              </div>
            ) : (
              <div
                className='grid'
                style={{
                  gridTemplateColumns: 'repeat(auto-fit, minmax(380px, 1fr))',
                  gap: 'clamp(1.5rem, 3vw, 2.5rem)',
                }}
              >
                {filteredAgents.map(agent => (
                  <AgentCard
                    key={agent.path}
                    agent={agent}
                    onToggle={handleToggleAgent}
                    onEdit={handleEditAgent}
                    canModify={user?.canModifyServers || false}
                    onRefreshSuccess={refreshAgentData}
                    onAgentUpdate={handleAgentUpdate}
                    authToken={agentApiToken}
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* External Registries Section */}
      {viewFilter === 'external' && (
        <div className='mb-8'>
          <h2 className='text-xl font-bold text-gray-900 dark:text-white mb-4'>External Registries</h2>
          <div className='relative'>
            {serverLoading && agentLoading && (
              <div className='absolute inset-0 bg-white/20 dark:bg-black/20 backdrop-blur-sm rounded-2xl flex items-center justify-center z-10'>
                <div className='animate-spin rounded-full h-8 w-8 border-b-2 border-cyan-600'></div>
              </div>
            )}
            {filteredExternalServers.length === 0 && filteredExternalAgents.length === 0 ? (
              <div className='text-center py-12 bg-gray-50 dark:bg-gray-800 rounded-lg border border-dashed border-gray-300 dark:border-gray-600'>
                <div className='text-gray-400 text-lg mb-2'>
                  {externalServers.length === 0 && externalAgents.length === 0
                    ? 'No External Registries Available'
                    : 'No Results Found'}
                </div>
                <p className='text-gray-500 dark:text-gray-300 text-sm max-w-md mx-auto'>
                  {externalServers.length === 0 && externalAgents.length === 0
                    ? 'External registry integrations (Anthropic, ASOR, and more) will be available soon'
                    : 'Press Enter in the search bar to search semantically'}
                </p>
              </div>
            ) : (
              <div>
                {/* External Servers */}
                {filteredExternalServers.length > 0 && (
                  <div className='mb-6'>
                    <h3 className='text-lg font-semibold text-gray-800 dark:text-gray-200 mb-3'>Servers</h3>
                    <div
                      className='grid'
                      style={{
                        gridTemplateColumns: 'repeat(auto-fit, minmax(380px, 1fr))',
                        gap: 'clamp(1.5rem, 3vw, 2.5rem)',
                      }}
                    >
                      {filteredExternalServers.map(server => (
                        <ServerCard
                          key={server.id}
                          server={server}
                          onEdit={handleEditServer}
                          onServerUpdate={handleServerUpdate}
                          onRefreshSuccess={refreshServerData}
                        />
                      ))}
                    </div>
                  </div>
                )}

                {/* External Agents */}
                {filteredExternalAgents.length > 0 && (
                  <div>
                    <h3 className='text-lg font-semibold text-gray-800 dark:text-gray-200 mb-3'>Agents</h3>
                    <div
                      className='grid'
                      style={{
                        gridTemplateColumns: 'repeat(auto-fit, minmax(380px, 1fr))',
                        gap: 'clamp(1.5rem, 3vw, 2.5rem)',
                      }}
                    >
                      {filteredExternalAgents.map(agent => (
                        <AgentCard
                          key={agent.path}
                          agent={agent}
                          onToggle={handleToggleAgent}
                          onEdit={handleEditAgent}
                          canModify={user?.canModifyServers || false}
                          onRefreshSuccess={refreshAgentData}
                          onAgentUpdate={handleAgentUpdate}
                        />
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Empty state when both are filtered out */}
      {((filteredServers.length === 0 && filteredAgents.length === 0) ||
        (viewFilter === 'servers' && filteredServers.length === 0) ||
        (viewFilter === 'agents' && filteredAgents.length === 0)) &&
        (searchTerm || activeFilter !== 'all') && (
          <div className='text-center py-16'>
            <div className='text-gray-400 text-xl mb-4'>No items found</div>
            <p className='text-gray-500 dark:text-gray-300 text-base max-w-md mx-auto'>
              Press Enter in the search bar to search semantically
            </p>
          </div>
        )}
    </>
  );

  const getCardNumber = () => {
    const serverLength = semanticSectionVisible ? semanticServers.length : filteredServers?.length || 0;
    const agentLength = semanticSectionVisible ? semanticAgents.length : filteredAgents?.length || 0;
    if (viewFilter === 'servers') {
      return `Showing ${serverLength} servers`;
    } else if (viewFilter === 'agents') {
      return `Showing ${agentLength} agents`;
    }
  };

  return (
    <>
      <div className='flex flex-col h-full'>
        {/* Fixed Header Section */}
        <div className='flex-shrink-0 space-y-4 pb-4'>
          {/* View Filter Tabs */}
          <div className='flex gap-2 border-b border-gray-200 dark:border-gray-700 overflow-x-auto'>
            <button
              onClick={() => handleChangeViewFilter('servers')}
              className={`px-4 py-2 text-sm font-medium whitespace-nowrap transition-colors border-b-2 ${
                viewFilter === 'servers'
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
                viewFilter === 'agents'
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
                viewFilter === 'external'
                  ? 'border-green-500 text-green-600 dark:text-green-400'
                  : 'border-transparent text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200'
              }`}
            >
              <HiServerStack className='h-6 w-6 inline mr-2' />
              External Registries
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

            <button onClick={handleRegisterServer} className='btn-primary flex items-center space-x-2 flex-shrink-0'>
              <PlusIcon className='h-4 w-4' />
              <span>Register</span>
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
                    <h4 className='text-base font-semibold text-gray-900 dark:text-gray-200'>
                      Keyword search fallback
                    </h4>
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

      {/* Edit Agent Modal */}
      {editingAgent && (
        <div className='fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center p-4 z-50'>
          <div className='bg-white dark:bg-gray-800 rounded-lg p-6 w-full max-w-md max-h-[90vh] overflow-y-auto'>
            <h3 className='text-lg font-semibold text-gray-900 dark:text-white mb-4'>
              Edit Agent: {editingAgent.name}
            </h3>

            <form
              onSubmit={async e => {
                e.preventDefault();
                await handleSaveEditAgent();
              }}
              className='space-y-4'
            >
              <div>
                <label className='block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1'>Agent Name *</label>
                <input
                  type='text'
                  value={editAgentForm.name}
                  onChange={e => setEditAgentForm(prev => ({ ...prev, name: e.target.value }))}
                  className='block w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-cyan-500 focus:border-cyan-500'
                  required
                />
              </div>

              <div>
                <label className='block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1'>Description</label>
                <textarea
                  value={editAgentForm.description}
                  onChange={e => setEditAgentForm(prev => ({ ...prev, description: e.target.value }))}
                  className='block w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-cyan-500 focus:border-cyan-500'
                  rows={3}
                  placeholder='Brief description of the agent'
                />
              </div>

              <div>
                <label className='block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1'>Version</label>
                <input
                  type='text'
                  value={editAgentForm.version}
                  onChange={e => setEditAgentForm(prev => ({ ...prev, version: e.target.value }))}
                  className='block w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-cyan-500 focus:border-cyan-500'
                  placeholder='1.0.0'
                />
              </div>

              <div>
                <label className='block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1'>Visibility</label>
                <select
                  value={editAgentForm.visibility}
                  onChange={e =>
                    setEditAgentForm(prev => ({
                      ...prev,
                      visibility: e.target.value as 'public' | 'private' | 'group-restricted',
                    }))
                  }
                  className='block w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-cyan-500 focus:border-cyan-500'
                >
                  <option value='private'>Private</option>
                  <option value='public'>Public</option>
                  <option value='group-restricted'>Group Restricted</option>
                </select>
              </div>

              <div>
                <label className='block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1'>Trust Level</label>
                <select
                  value={editAgentForm.trust_level}
                  onChange={e =>
                    setEditAgentForm(prev => ({
                      ...prev,
                      trust_level: e.target.value as 'community' | 'verified' | 'trusted' | 'unverified',
                    }))
                  }
                  className='block w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-cyan-500 focus:border-cyan-500'
                >
                  <option value='unverified'>Unverified</option>
                  <option value='community'>Community</option>
                  <option value='verified'>Verified</option>
                  <option value='trusted'>Trusted</option>
                </select>
              </div>

              <div>
                <label className='block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1'>Tags</label>
                <input
                  type='text'
                  value={editAgentForm.tags.join(',')}
                  onChange={e =>
                    setEditAgentForm(prev => ({
                      ...prev,
                      tags: e.target.value
                        .split(',')
                        .map(t => t.trim())
                        .filter(t => t),
                    }))
                  }
                  className='block w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-cyan-500 focus:border-cyan-500'
                  placeholder='tag1,tag2,tag3'
                />
              </div>

              <div>
                <label className='block text-sm font-medium text-gray-700 dark:text-gray-200 mb-1'>
                  Path (read-only)
                </label>
                <input
                  type='text'
                  value={editAgentForm.path}
                  className='block w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-300'
                  disabled
                />
              </div>

              <div className='flex space-x-3 pt-4'>
                <button
                  type='submit'
                  disabled={editAgentLoading}
                  className='flex-1 px-4 py-2 text-sm font-medium text-white bg-cyan-600 hover:bg-cyan-700 disabled:opacity-50 rounded-md transition-colors'
                >
                  {editAgentLoading ? 'Saving...' : 'Save Changes'}
                </button>
                <button
                  onClick={handleCloseEdit}
                  className='flex-1 px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 rounded-md transition-colors'
                >
                  Cancel
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </>
  );
};

export default Dashboard;
