import { ArrowPathIcon, CogIcon, InformationCircleIcon } from '@heroicons/react/24/outline';
import axios from 'axios';
import type React from 'react';
import { useState } from 'react';

import type { ServerInfo } from '@/contexts/ServerContext';
import type { Agent as AgentType } from '@/services/agent/type';
import type { SemanticAgentHit, SemanticServerHit, SemanticToolHit } from '../hooks/useSemanticSearch';
import AgentDetailsModal from './AgentDetailsModal';
import IconButton from './IconButton';
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
    id: hit.path || '',
    name: hit.agentName,
    path: hit.path,
    url: hit.url || (hit.agentCard as any)?.url || '',
    description: hit.description || '',
    version: (hit as any).version || '',
    protocolVersion: '',
    capabilities: { streaming: false, pushNotifications: false },
    skills: [],
    securitySchemes: { bearer: { type: '', scheme: '' } },
    preferredTransport: '',
    defaultInputModes: [],
    defaultOutputModes: [],
    provider: { organization: '', url: '' },
    permissions: { VIEW: false, EDIT: false, DELETE: false, SHARE: false },
    author: '',
    wellKnown: { enabled: false, url: '', lastSyncAt: '', lastSyncStatus: '', lastSyncVersion: '' },
    createdAt: '',
    updatedAt: '',
    enabled: hit.isEnabled ?? true,
    tags: hit.tags,
    status: 'unknown' as any,
  });

  return (
    <>
      <div className='space-y-8'>
        <div className='flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between'>
          <div>
            <p className='text-sm font-medium uppercase tracking-wide text-[var(--jarvis-muted)]'>Semantic Search</p>
            <h3 className='text-xl font-semibold text-[var(--jarvis-text-strong)]'>
              Results for <span className='text-[var(--jarvis-primary-text)]'>“{query}”</span>
            </h3>
          </div>
          {loading && (
            <div className='inline-flex items-center text-sm text-[var(--jarvis-primary-text)]'>
              <ArrowPathIcon className='h-5 w-5 animate-spin mr-2' />
              Searching…
            </div>
          )}
        </div>

        {error && (
          <div className='rounded-2xl border border-[var(--jarvis-danger)]/30 bg-[var(--jarvis-danger-soft)] px-4 py-3 text-sm text-[var(--jarvis-danger-text)]'>
            {error}
          </div>
        )}

        {!loading && !error && !hasResults && (
          <div className='rounded-2xl border border-dashed border-[var(--jarvis-border)] bg-[var(--jarvis-surface)]/70 py-16 text-center'>
            <p className='mb-2 text-lg font-medium text-[var(--jarvis-text)]'>No semantic matches found</p>
            <p className='mx-auto max-w-xl text-sm text-[var(--jarvis-muted)]'>
              Try refining your query or describing the tools or capabilities you need. Semantic search understands
              natural language — phrases like “servers that handle authentication” or “tools for syncing calendars” work
              great.
            </p>
          </div>
        )}

        {servers.length > 0 && (
          <section className='space-y-4'>
            <div className='flex items-center justify-between'>
              <h4 className='text-lg font-semibold text-[var(--jarvis-text-strong)]'>
                Matching Servers{' '}
                <span className='text-sm font-normal text-[var(--jarvis-muted)]'>({servers.length})</span>
              </h4>
            </div>
            <div
              className='grid'
              style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '1.5rem' }}
            >
              {servers.map(server => (
                <div
                  key={server.path}
                  className='rounded-2xl border border-[var(--jarvis-border)] bg-[var(--jarvis-card)] p-5 shadow-sm transition-shadow hover:shadow-md'
                >
                  <div className='flex items-start justify-between gap-4'>
                    <div>
                      <p className='text-base font-semibold text-[var(--jarvis-text-strong)]'>{server.serverName}</p>
                      <p className='text-sm text-[var(--jarvis-muted)]'>{server.path}</p>
                    </div>
                    <div className='flex items-center gap-2'>
                      <IconButton
                        onClick={() => setConfigServer(server)}
                        ariaLabel='Open MCP configuration'
                        tooltip='Open MCP configuration'
                        size='card'
                        className='text-[var(--jarvis-icon)] hover:text-[var(--jarvis-icon-hover)]'
                      >
                        <CogIcon className='h-4 w-4' />
                      </IconButton>
                      <span className='inline-flex items-center rounded-full border border-[var(--jarvis-primary)]/30 bg-[var(--jarvis-primary-soft)] px-3 py-1 text-xs font-semibold text-[var(--jarvis-primary-text)]'>
                        {formatPercent(server.relevanceScore)} match
                      </span>
                    </div>
                  </div>
                  <p className='mt-3 line-clamp-3 text-sm text-[var(--jarvis-muted)]'>
                    {server.description || server.matchContext || 'No description available.'}
                  </p>

                  {server.tags?.length > 0 && (
                    <div className='mt-4 flex flex-wrap gap-2'>
                      {server.tags.slice(0, 6).map(tag => (
                        <span
                          key={tag}
                          className='rounded-full border border-[var(--jarvis-border-soft)] bg-[var(--jarvis-surface)] px-2.5 py-1 text-xs text-[var(--jarvis-text)]'
                        >
                          {tag}
                        </span>
                      ))}
                    </div>
                  )}

                  {server.matchingTools?.length > 0 && (
                    <div className='mt-4 border-t border-dashed border-[var(--jarvis-border)] pt-3'>
                      <p className='mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--jarvis-muted)]'>
                        Relevant tools
                      </p>
                      <ul className='space-y-2'>
                        {server.matchingTools.slice(0, 3).map(tool => (
                          <li key={tool.toolName} className='text-sm text-[var(--jarvis-text)]'>
                            <span className='font-medium text-[var(--jarvis-text-strong)]'>{tool.toolName}</span>
                            <span className='mx-2 text-[var(--jarvis-faint)]'>•</span>
                            <span className='text-[var(--jarvis-muted)]'>
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
              <h4 className='text-lg font-semibold text-[var(--jarvis-text-strong)]'>
                Matching Tools <span className='text-sm font-normal text-[var(--jarvis-muted)]'>({tools.length})</span>
              </h4>
            </div>
            <div
              className='grid'
              style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '1.25rem' }}
            >
              {tools.map(tool => (
                <div
                  key={`${tool.serverPath}-${tool.toolName}`}
                  className='flex flex-col gap-2 rounded-2xl border border-[var(--jarvis-border)] bg-[var(--jarvis-card)] p-4 sm:flex-row sm:items-center sm:justify-between'
                >
                  <div>
                    <p className='text-sm font-semibold text-[var(--jarvis-text-strong)]'>
                      {tool.toolName}
                      <span className='ml-2 text-xs font-normal text-[var(--jarvis-muted)]'>({tool.serverName})</span>
                    </p>
                    <p className='text-sm text-[var(--jarvis-muted)]'>
                      {tool.description || tool.matchContext || 'No description available.'}
                    </p>
                  </div>
                  <span className='inline-flex items-center rounded-full border border-[var(--jarvis-border-soft)] bg-[var(--jarvis-surface)] px-3 py-1 text-xs font-semibold text-[var(--jarvis-text)]'>
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
              <h4 className='text-lg font-semibold text-[var(--jarvis-text-strong)]'>
                Matching Agents{' '}
                <span className='text-sm font-normal text-[var(--jarvis-muted)]'>({agents.length})</span>
              </h4>
            </div>
            <div
              className='grid'
              style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '1.25rem' }}
            >
              {agents.map(agent => (
                <div
                  key={agent.path}
                  className='rounded-2xl border border-[var(--jarvis-border)] bg-[var(--jarvis-card)] p-5 shadow-sm transition-shadow hover:shadow-md'
                >
                  <div className='flex items-start justify-between gap-4'>
                    <div>
                      <p className='text-base font-semibold text-[var(--jarvis-text-strong)]'>{agent.agentName}</p>
                      <p className='text-xs uppercase tracking-wide text-[var(--jarvis-faint)]'>
                        {agent.visibility || 'public'}
                      </p>
                    </div>
                    <div className='flex items-center gap-2'>
                      <IconButton
                        onClick={() => openAgentDetails(agent)}
                        ariaLabel='View full agent details'
                        tooltip='View full agent details'
                        size='card'
                        className='text-[var(--jarvis-icon)] hover:text-[var(--jarvis-icon-hover)]'
                      >
                        <InformationCircleIcon className='h-4 w-4' />
                      </IconButton>
                      <span className='inline-flex items-center rounded-full border border-[var(--jarvis-info-text)]/25 bg-[var(--jarvis-info-soft)] px-3 py-1 text-xs font-semibold text-[var(--jarvis-info-text)]'>
                        {formatPercent(agent.relevanceScore)} match
                      </span>
                    </div>
                  </div>

                  <p className='mt-3 line-clamp-3 text-sm text-[var(--jarvis-muted)]'>
                    {agent.description || agent.matchContext || 'No description available.'}
                  </p>

                  {agent.skills?.length > 0 && (
                    <div className='mt-4'>
                      <p className='mb-1 text-xs font-semibold uppercase tracking-wide text-[var(--jarvis-muted)]'>
                        Key Skills
                      </p>
                      <p className='text-xs text-[var(--jarvis-muted)]'>
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
                          className='rounded-full border border-[var(--jarvis-info-text)]/20 bg-[var(--jarvis-info-soft)] px-2.5 py-1 text-[11px] text-[var(--jarvis-info-text)]'
                        >
                          {tag}
                        </span>
                      ))}
                    </div>
                  )}

                  <div className='mt-4 flex items-center justify-between text-xs text-[var(--jarvis-muted)]'>
                    <span className='font-semibold text-[var(--jarvis-info-text)]'>
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
