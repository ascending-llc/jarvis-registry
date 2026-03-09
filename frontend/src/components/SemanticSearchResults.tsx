import { ArrowPathIcon, CogIcon, InformationCircleIcon } from '@heroicons/react/24/outline';
import axios from 'axios';
import type React from 'react';
import { useState } from 'react';
import type { ServerInfo } from '@/contexts/ServerContext';
import type { SemanticAgentHit, SemanticServerHit, SemanticToolHit } from '../hooks/useSemanticSearch';
import type { Agent as AgentType } from './AgentCard';
import AgentDetailsModal from './AgentDetailsModal';
import ServerConfigModal from './ServerConfigModal';

interface SemanticSearchResultsProps {
  query: string;
  loading: boolean;
  error: string | null;
  servers: SemanticServerHit[];
  tools: SemanticToolHit[];
  agents: SemanticAgentHit[];
}

const formatPercent = (value: number) => `${Math.round(Math.min(value, 1) * 100)}%`;

const SemanticSearchResults: React.FC<SemanticSearchResultsProps> = ({
  query,
  loading,
  error,
  servers,
  tools,
  agents,
}) => {
  const hasResults = servers.length > 0 || tools.length > 0 || agents.length > 0;
  const [configServer, setConfigServer] = useState<SemanticServerHit | null>(null);
  const [detailsAgent, setDetailsAgent] = useState<SemanticAgentHit | null>(null);
  const [agentDetailsData, setAgentDetailsData] = useState<any>(null);
  const [agentDetailsLoading, setAgentDetailsLoading] = useState(false);

  const openAgentDetails = async (agentHit: SemanticAgentHit) => {
    setDetailsAgent(agentHit);
    setAgentDetailsData(null);
    setAgentDetailsLoading(true);
    try {
      const response = await axios.get(`/api/agents${agentHit.path}`);
      setAgentDetailsData(response.data);
    } catch (error) {
      console.error('Failed to fetch agent details:', error);
    } finally {
      setAgentDetailsLoading(false);
    }
  };

  const mapHitToAgent = (hit: SemanticAgentHit): AgentType => ({
    name: hit.agentName,
    path: hit.path,
    url: hit.url || (hit.agentCard as any)?.url,
    description: hit.description,
    version: (hit as any).version,
    visibility: (hit.visibility as AgentType['visibility']) ?? 'public',
    trust_level: (hit.trustLevel as AgentType['trust_level']) ?? 'unverified',
    enabled: hit.isEnabled ?? true,
    tags: hit.tags,
    status: 'unknown',
  });

  return (
    <>
      <div className='space-y-8'>
        <div className='flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between'>
          <div>
            <p className='text-sm font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide'>
              Semantic Search
            </p>
            <h3 className='text-xl font-semibold text-gray-900 dark:text-white'>
              Results for <span className='text-purple-600 dark:text-purple-300'>“{query}”</span>
            </h3>
          </div>
          {loading && (
            <div className='inline-flex items-center text-sm text-purple-600 dark:text-purple-300'>
              <ArrowPathIcon className='h-5 w-5 animate-spin mr-2' />
              Searching…
            </div>
          )}
        </div>

        {error && (
          <div className='rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 dark:border-red-500/40 dark:bg-red-900/30 dark:text-red-200'>
            {error}
          </div>
        )}

        {!loading && !error && !hasResults && (
          <div className='text-center py-16 border border-dashed border-gray-200 dark:border-gray-700 rounded-xl'>
            <p className='text-lg font-medium text-gray-700 dark:text-gray-200 mb-2'>No semantic matches found</p>
            <p className='text-sm text-gray-500 dark:text-gray-400 max-w-xl mx-auto'>
              Try refining your query or describing the tools or capabilities you need. Semantic search understands
              natural language — phrases like “servers that handle authentication” or “tools for syncing calendars” work
              great.
            </p>
          </div>
        )}

        {servers.length > 0 && (
          <section className='space-y-4'>
            <div className='flex items-center justify-between'>
              <h4 className='text-lg font-semibold text-gray-900 dark:text-gray-100'>
                Matching Servers <span className='text-sm font-normal text-gray-500'>({servers.length})</span>
              </h4>
            </div>
            <div
              className='grid'
              style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '1.5rem' }}
            >
              {servers.map(server => (
                <div
                  key={server.path}
                  className='rounded-2xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-5 shadow-sm hover:shadow-md transition-shadow'
                >
                  <div className='flex items-start justify-between gap-4'>
                    <div>
                      <p className='text-base font-semibold text-gray-900 dark:text-white'>{server.serverName}</p>
                      <p className='text-sm text-gray-500 dark:text-gray-300'>{server.path}</p>
                    </div>
                    <div className='flex items-center gap-2'>
                      <button
                        onClick={() => setConfigServer(server)}
                        className='p-2 text-gray-400 hover:text-green-600 dark:hover:text-green-300 hover:bg-green-50 dark:hover:bg-green-700/30 rounded-lg transition-colors'
                        title='Open MCP configuration'
                      >
                        <CogIcon className='h-4 w-4' />
                      </button>
                      <span className='inline-flex items-center rounded-full bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-200 px-3 py-1 text-xs font-semibold'>
                        {formatPercent(server.relevanceScore)} match
                      </span>
                    </div>
                  </div>
                  <p className='mt-3 text-sm text-gray-600 dark:text-gray-300 line-clamp-3'>
                    {server.description || server.matchContext || 'No description available.'}
                  </p>

                  {server.tags?.length > 0 && (
                    <div className='mt-4 flex flex-wrap gap-2'>
                      {server.tags.slice(0, 6).map(tag => (
                        <span
                          key={tag}
                          className='px-2.5 py-1 text-xs rounded-full bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-200'
                        >
                          {tag}
                        </span>
                      ))}
                    </div>
                  )}

                  {server.matchingTools?.length > 0 && (
                    <div className='mt-4 border-t border-dashed border-gray-200 dark:border-gray-700 pt-3'>
                      <p className='text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-2'>
                        Relevant tools
                      </p>
                      <ul className='space-y-2'>
                        {server.matchingTools.slice(0, 3).map(tool => (
                          <li key={tool.toolName} className='text-sm text-gray-700 dark:text-gray-200'>
                            <span className='font-medium text-gray-900 dark:text-white'>{tool.toolName}</span>
                            <span className='mx-2 text-gray-400'>•</span>
                            <span className='text-gray-600 dark:text-gray-300'>
                              {tool.description || tool.matchContext || 'No description'}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </section>
        )}

        {tools.length > 0 && (
          <section className='space-y-4'>
            <div className='flex items-center justify-between'>
              <h4 className='text-lg font-semibold text-gray-900 dark:text-gray-100'>
                Matching Tools <span className='text-sm font-normal text-gray-500'>({tools.length})</span>
              </h4>
            </div>
            <div
              className='grid'
              style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '1.25rem' }}
            >
              {tools.map(tool => (
                <div
                  key={`${tool.serverPath}-${tool.toolName}`}
                  className='rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-4 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between'
                >
                  <div>
                    <p className='text-sm font-semibold text-gray-900 dark:text-white'>
                      {tool.toolName}
                      <span className='ml-2 text-xs font-normal text-gray-500 dark:text-gray-400'>
                        ({tool.serverName})
                      </span>
                    </p>
                    <p className='text-sm text-gray-600 dark:text-gray-300'>
                      {tool.description || tool.matchContext || 'No description available.'}
                    </p>
                  </div>
                  <span className='inline-flex items-center rounded-full bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-200 px-3 py-1 text-xs font-semibold'>
                    {formatPercent(tool.relevanceScore)} match
                  </span>
                </div>
              ))}
            </div>
          </section>
        )}

        {agents.length > 0 && (
          <section className='space-y-4'>
            <div className='flex items-center justify-between'>
              <h4 className='text-lg font-semibold text-gray-900 dark:text-gray-100'>
                Matching Agents <span className='text-sm font-normal text-gray-500'>({agents.length})</span>
              </h4>
            </div>
            <div
              className='grid'
              style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '1.25rem' }}
            >
              {agents.map(agent => (
                <div
                  key={agent.path}
                  className='rounded-2xl border border-cyan-200 dark:border-cyan-900/40 bg-white dark:bg-gray-800 p-5 shadow-sm hover:shadow-md transition-shadow'
                >
                  <div className='flex items-start justify-between gap-4'>
                    <div>
                      <p className='text-base font-semibold text-gray-900 dark:text-white'>{agent.agentName}</p>
                      <p className='text-xs uppercase tracking-wide text-gray-400 dark:text-gray-500'>
                        {agent.visibility || 'public'}
                      </p>
                    </div>
                    <div className='flex items-center gap-2'>
                      <button
                        onClick={() => openAgentDetails(agent)}
                        className='p-2 text-gray-400 hover:text-cyan-600 dark:hover:text-cyan-300 hover:bg-cyan-50 dark:hover:bg-cyan-700/30 rounded-lg transition-colors'
                        title='View full agent details'
                      >
                        <InformationCircleIcon className='h-4 w-4' />
                      </button>
                      <span className='inline-flex items-center rounded-full bg-cyan-100 text-cyan-700 dark:bg-cyan-900/40 dark:text-cyan-200 px-3 py-1 text-xs font-semibold'>
                        {formatPercent(agent.relevanceScore)} match
                      </span>
                    </div>
                  </div>

                  <p className='mt-3 text-sm text-gray-600 dark:text-gray-300 line-clamp-3'>
                    {agent.description || agent.matchContext || 'No description available.'}
                  </p>

                  {agent.skills?.length > 0 && (
                    <div className='mt-4'>
                      <p className='text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-1'>
                        Key Skills
                      </p>
                      <p className='text-xs text-gray-600 dark:text-gray-300'>
                        {agent.skills.slice(0, 4).join(', ')}
                        {agent.skills.length > 4 && '…'}
                      </p>
                    </div>
                  )}

                  {agent.tags?.length > 0 && (
                    <div className='mt-4 flex flex-wrap gap-2'>
                      {agent.tags.slice(0, 6).map(tag => (
                        <span
                          key={tag}
                          className='px-2.5 py-1 text-[11px] rounded-full bg-cyan-50 text-cyan-700 dark:bg-cyan-900/40 dark:text-cyan-200'
                        >
                          {tag}
                        </span>
                      ))}
                    </div>
                  )}

                  <div className='mt-4 flex items-center justify-between text-xs text-gray-500 dark:text-gray-400'>
                    <span className='font-semibold text-cyan-700 dark:text-cyan-200'>
                      {agent.trustLevel || 'unverified'}
                    </span>
                    <span>{agent.isEnabled ? 'Enabled' : 'Disabled'}</span>
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}
      </div>

      {configServer && (
        <ServerConfigModal
          server={
            {
              name: configServer.serverName,
              path: configServer.path,
              description: configServer.description,
              enabled: configServer.isEnabled ?? true,
              tags: configServer.tags,
              numTools: configServer.numTools,
            } as ServerInfo
          }
          isOpen
          onClose={() => setConfigServer(null)}
        />
      )}

      {detailsAgent && (
        <AgentDetailsModal
          agent={mapHitToAgent(detailsAgent)}
          isOpen
          onClose={() => setDetailsAgent(null)}
          loading={agentDetailsLoading}
          fullDetails={agentDetailsData}
        />
      )}
    </>
  );
};

export default SemanticSearchResults;
