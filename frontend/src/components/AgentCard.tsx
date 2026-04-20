import { PencilSquareIcon, WrenchScrewdriverIcon, XMarkIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import agentcoreIcon from '@/assets/agentcore.svg';
import IconButton from '@/components/IconButton';
import { useGlobal } from '@/contexts/GlobalContext';
import { useServer } from '@/contexts/ServerContext';
import SERVICES from '@/services';
import type { Agent, AgentItem } from '@/services/agent/type';

/**
 * Props for the AgentCard component.
 */
interface AgentCardProps {
  agent: (Agent | AgentItem) & { [key: string]: any }; // Allow additional fields from full agent JSON
}

interface ParsedSkillExample {
  label: string;
  prettyJson: string | null;
}

const parseSkillExample = (example: string): ParsedSkillExample => {
  if (typeof example !== 'string' || !example) {
    return { label: '', prettyJson: null };
  }
  const colonIndex = example.indexOf(':');
  let label = '';
  let content = example;

  if (colonIndex !== -1) {
    const potentialLabel = example.substring(0, colonIndex).trim();
    const potentialContent = example.substring(colonIndex + 1).trim();
    if (potentialContent.startsWith('{') || potentialContent.startsWith('[')) {
      label = potentialLabel;
      content = potentialContent;
    }
  }

  try {
    const parsed = JSON.parse(content);
    return {
      label,
      prettyJson: JSON.stringify(parsed, null, 2),
    };
  } catch {
    return {
      label: '',
      prettyJson: null,
    };
  }
};

/**
 * AgentCard component for displaying A2A agents.
 */
const AgentCard: React.FC<AgentCardProps> = ({ agent }) => {
  const navigate = useNavigate();
  const { showToast } = useGlobal();
  const { handleAgentUpdate } = useServer();
  const [loading, setLoading] = useState(false);
  const [showSkills, setShowSkills] = useState(false);
  const canEdit = !!agent.permissions?.EDIT;

  const numSkills = 'numSkills' in agent ? agent.numSkills : agent.skills?.length || 0;
  const hasSkillsDetails = 'skills' in agent && Array.isArray(agent.skills) && agent.skills.length > 0;

  const hasAgentCoreTags =
    agent.tags?.includes('federated') && agent.tags?.includes('aws') && agent.tags?.includes('agentcore');

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && showSkills) {
        setShowSkills(false);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [showSkills]);

  const toEditPage = (agent: Agent | AgentItem) => {
    navigate(`/agent-edit?id=${(agent as any).id || agent.path}`);
  };

  const handleToggleAgent = async (id: string, enabled: boolean) => {
    try {
      setLoading(true);
      await SERVICES.AGENT.toggleAgentState(id, { enabled });
      handleAgentUpdate(id, { enabled });
      showToast(`Agent ${enabled ? 'enabled' : 'disabled'} successfully!`, 'success');
    } catch (error: any) {
      const errorMessage = error.detail?.message || (typeof error.detail === 'string' ? error.detail : '');
      showToast(errorMessage || 'Failed to toggle agent', 'error');
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <div className='group relative flex h-full flex-col rounded-2xl border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] shadow-sm transition-all duration-300 hover:border-[color:var(--jarvis-border-strong)] hover:shadow-xl'>
        {loading && (
          <div className='absolute inset-0 z-10 flex items-center justify-center rounded-2xl bg-[var(--jarvis-overlay)] backdrop-blur-sm'>
            <div className='h-8 w-8 animate-spin rounded-full border-b-2 border-[var(--jarvis-spinner)]'></div>
          </div>
        )}

        {/* Header */}
        <div className='p-4 pb-3'>
          <div className='flex items-start justify-between mb-3'>
            <div className='flex-1 min-w-0'>
              <div className='flex flex-wrap items-center gap-1.5 mb-2'>
                {agent.permissions?.VIEW ? (
                  <h3
                    className='max-w-[160px] cursor-pointer truncate text-base font-medium text-[var(--jarvis-text)] transition-colors hover:text-[var(--jarvis-text-strong)]'
                    onClick={() => navigate(`/agent-edit?id=${agent.id}&isReadOnly=true`)}
                  >
                    {agent.name}
                  </h3>
                ) : (
                  <h3 className='max-w-[160px] truncate text-base font-medium text-[var(--jarvis-text)]'>
                    {agent.name}
                  </h3>
                )}
              </div>
            </div>

            <div className='flex gap-1'>
              {agent.permissions?.EDIT && (
                <IconButton
                  ariaLabel='Edit agent'
                  tooltip='Edit'
                  onClick={() => toEditPage?.(agent)}
                  size='card'
                  className='text-[var(--jarvis-icon)] hover:bg-[var(--jarvis-primary-soft)] hover:text-[var(--jarvis-icon-hover)]'
                >
                  <PencilSquareIcon className='h-3.5 w-3.5' />
                </IconButton>
              )}
            </div>
          </div>

          {/* Description */}
          <p className='mb-3 line-clamp-2 text-xs leading-relaxed text-[var(--jarvis-subtle)]'>
            {agent.description || 'No description available'}
          </p>

          {/* Tags */}
          {agent.tags && agent.tags.length > 0 && (
            <div className='flex flex-wrap gap-1 mb-3 max-h-10 overflow-hidden'>
              {agent.tags.slice(0, 3).map(tag => (
                <span
                  key={tag}
                  className='max-w-[100px] truncate rounded bg-[var(--jarvis-info-soft)] px-1.5 py-0.5 text-xs font-medium text-[var(--jarvis-info-text)]'
                >
                  #{tag}
                </span>
              ))}
              {agent.tags.length > 3 && (
                <span className='rounded bg-[var(--jarvis-card-muted)] px-1.5 py-0.5 text-xs font-medium text-[var(--jarvis-subtle)]'>
                  +{agent.tags.length - 3}
                </span>
              )}
            </div>
          )}
        </div>

        {/* Skills */}
        <div className='px-4 pb-3'>
          <div className='grid grid-cols-2 gap-2'>
            <div className='flex items-center gap-1.5'>
              {numSkills > 0 ? (
                hasSkillsDetails ? (
                  <button
                    onClick={() => setShowSkills(true)}
                    className='-mx-1.5 -my-0.5 flex items-center gap-1.5 rounded px-1.5 py-0.5 text-xs text-[var(--jarvis-info-text)] transition-all hover:bg-[var(--jarvis-info-soft)] hover:text-[var(--jarvis-icon-hover)]'
                    title='View skills'
                  >
                    <div className='rounded bg-[var(--jarvis-card-muted)] p-1'>
                      <WrenchScrewdriverIcon className='h-3.5 w-3.5' />
                    </div>
                    <div>
                      <div className='text-xs font-semibold'>{numSkills}</div>
                      <div className='text-xs'>Skills</div>
                    </div>
                  </button>
                ) : (
                  <div
                    className='-mx-1.5 -my-0.5 flex items-center gap-1.5 rounded px-1.5 py-0.5 text-xs text-[var(--jarvis-info-text)]'
                    title='Skills count'
                  >
                    <div className='rounded bg-[var(--jarvis-card-muted)] p-1'>
                      <WrenchScrewdriverIcon className='h-3.5 w-3.5' />
                    </div>
                    <div>
                      <div className='text-xs font-semibold'>{numSkills}</div>
                      <div className='text-xs'>Skills</div>
                    </div>
                  </div>
                )
              ) : (
                <div className='flex items-center gap-1.5 text-[var(--jarvis-faint)]'>
                  <div className='rounded bg-[var(--jarvis-card-muted)] p-1'>
                    <WrenchScrewdriverIcon className='h-3.5 w-3.5' />
                  </div>
                  <div>
                    <div className='text-xs font-semibold'>0</div>
                    <div className='text-xs'>Skills</div>
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
                  className={`h-2.5 w-2.5 rounded-full ${
                    agent.enabled
                      ? 'bg-[var(--jarvis-success)] shadow-lg shadow-emerald-500/30'
                      : 'bg-[var(--jarvis-faint)]'
                  }`}
                />
                <span className='text-xs font-medium text-[var(--jarvis-muted)]'>
                  {agent.enabled ? 'Enabled' : 'Disabled'}
                </span>
              </div>

              <div className='h-3 w-px bg-[color:var(--jarvis-border)]' />

              <div className='flex items-center gap-1'>
                <div
                  className={`w-2.5 h-2.5 rounded-full ${
                    agent.status === 'active'
                      ? 'bg-[var(--jarvis-success)] shadow-lg shadow-emerald-500/30'
                      : agent.status === 'inactive'
                        ? 'bg-[var(--jarvis-warning)] shadow-lg shadow-amber-500/30'
                        : agent.status === 'error'
                          ? 'bg-[var(--jarvis-danger)] shadow-lg shadow-red-500/30'
                          : 'bg-[var(--jarvis-warning)] shadow-lg shadow-amber-500/30'
                  }`}
                />
                <span className='max-w-[80px] truncate text-xs font-medium text-[var(--jarvis-muted)]'>
                  {agent.status === 'active'
                    ? 'Active'
                    : agent.status === 'inactive'
                      ? 'Inactive'
                      : agent.status === 'error'
                        ? 'Error'
                        : 'Unknown'}
                </span>
              </div>
            </div>

            {/* Controls */}
            <div className='flex items-center gap-2'>
              {/* Toggle Switch */}
              <label
                className={`relative inline-flex items-center ${canEdit ? 'cursor-pointer' : 'cursor-not-allowed opacity-60'}`}
                onClick={e => e.stopPropagation()}
                title={canEdit ? 'Toggle agent status' : 'No edit permission'}
              >
                <input
                  type='checkbox'
                  checked={agent.enabled}
                  className='sr-only peer'
                  disabled={!canEdit || loading}
                  onChange={e => {
                    e.stopPropagation();
                    if (!canEdit) return;
                    handleToggleAgent(agent.id, e.target.checked);
                  }}
                />
                <div
                  className={`relative w-7 h-4 rounded-full transition-colors duration-200 ease-in-out ${
                    agent.enabled ? 'bg-[var(--jarvis-primary)]' : 'bg-[var(--jarvis-faint)]'
                  }`}
                >
                  <div
                    className={`absolute top-0.5 left-0 w-3 h-3 bg-white rounded-full transition-transform duration-200 ease-in-out ${
                      agent.enabled ? 'translate-x-4' : 'translate-x-0'
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
            className='absolute bottom-12 right-3 h-5 w-5'
            title='AWS AgentCore'
          />
        )}
      </div>

      {/* Skills Modal */}
      {showSkills && (
        <div className='fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm'>
          <div className='max-h-[80vh] w-full max-w-2xl overflow-auto rounded-xl bg-[var(--jarvis-card)] p-6 pt-0 text-[var(--jarvis-text)] shadow-xl'>
            <div className='sticky top-0 z-10 -mx-6 -mt-6 mb-4 flex items-center justify-between border-b border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] px-6 pb-2 pt-6'>
              <h3 className='text-lg font-semibold text-[var(--jarvis-text-strong)]'>Skills for {agent.name}</h3>
              <IconButton
                ariaLabel='Close'
                tooltip='Close'
                onClick={() => setShowSkills(false)}
                size='card'
                className='text-[var(--jarvis-icon)] transition-colors hover:text-[var(--jarvis-icon-hover)]'
              >
                <XMarkIcon className='h-6 w-6' />
              </IconButton>
            </div>

            <div className='space-y-4 mt-[2.8rem]'>
              {(agent as Agent).skills?.length > 0 ? (
                (agent as Agent).skills.map((skill: any, index: number) => (
                  <div
                    key={skill.id || index}
                    className='rounded-lg border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card-muted)] p-4'
                  >
                    <h4 className='mb-2 font-medium text-[var(--jarvis-text-strong)]'>{skill.name}</h4>
                    {skill.description && (
                      <p className='mb-2 text-sm text-[var(--jarvis-muted)]'>{skill.description}</p>
                    )}
                    {skill.tags && skill.tags.length > 0 && (
                      <div className='flex flex-wrap gap-1 mb-2'>
                        {skill.tags.map((tag: any) => (
                          <span
                            key={tag}
                            className='rounded bg-[var(--jarvis-info-soft)] px-1.5 py-0.5 text-xs font-medium text-[var(--jarvis-info-text)]'
                          >
                            #{tag}
                          </span>
                        ))}
                      </div>
                    )}
                    {(skill.inputModes?.length > 0 || skill.outputModes?.length > 0) && (
                      <details className='text-xs'>
                        <summary className='cursor-pointer text-[var(--jarvis-muted)]'>View Modes</summary>
                        <div className='mt-2 space-y-1 rounded border border-[color:var(--jarvis-border)] bg-[var(--jarvis-surface)] p-3 text-[var(--jarvis-text)]'>
                          {skill.inputModes?.length > 0 && (
                            <div>
                              <span className='font-medium'>Input:</span> {skill.inputModes.join(', ')}
                            </div>
                          )}
                          {skill.outputModes?.length > 0 && (
                            <div>
                              <span className='font-medium'>Output:</span> {skill.outputModes.join(', ')}
                            </div>
                          )}
                        </div>
                        {skill.examples && skill.examples.length > 0 && (
                          <div className='mt-3 space-y-2'>
                            <div className='flex items-center gap-1 text-xs font-semibold text-[var(--jarvis-muted)]'>
                              <span className='h-3 w-1 rounded-full bg-[var(--jarvis-info-text)]'></span>
                              Examples
                            </div>
                            <div className='space-y-3'>
                              {skill.examples.map((example: any, i: number) => {
                                const parsedExample = parseSkillExample(example);

                                if (parsedExample.prettyJson) {
                                  return (
                                    <div
                                      key={i}
                                      className='overflow-hidden rounded-lg border border-[color:var(--jarvis-border)]'
                                    >
                                      {parsedExample.label && (
                                        <div className='border-b border-[color:var(--jarvis-border)] bg-[var(--jarvis-surface)] px-2 py-1 text-[10px] font-bold uppercase tracking-wider text-[var(--jarvis-muted)]'>
                                          {parsedExample.label}
                                        </div>
                                      )}
                                      <pre className='overflow-x-auto bg-[var(--jarvis-card-muted)] p-2.5 text-[11px] font-mono text-[var(--jarvis-info-text)]'>
                                        {parsedExample.prettyJson}
                                      </pre>
                                    </div>
                                  );
                                }

                                return (
                                  <div
                                    key={i}
                                    className='break-all rounded-lg border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card-muted)] p-2.5 text-[11px] italic text-[var(--jarvis-muted)]'
                                  >
                                    {example}
                                  </div>
                                );
                              })}
                            </div>
                          </div>
                        )}
                      </details>
                    )}
                  </div>
                ))
              ) : (
                <p className='text-[var(--jarvis-muted)]'>No skills available for this agent.</p>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
};

export default AgentCard;
