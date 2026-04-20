import { PencilIcon, WrenchScrewdriverIcon, XMarkIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { useEffect, useState } from 'react';
import { FaAws } from 'react-icons/fa';
import { useNavigate } from 'react-router-dom';
import agentcoreIcon from '@/assets/agentcore.svg';
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
      <div className='group rounded-2xl shadow-sm hover:shadow-xl transition-all duration-300 h-full flex flex-col relative bg-white dark:bg-gray-800 border border-gray-100 dark:border-gray-700 hover:border-gray-200 dark:hover:border-gray-600'>
        {loading && (
          <div className='absolute inset-0 bg-white/20 dark:bg-black/20 backdrop-blur-sm rounded-2xl flex items-center justify-center z-10'>
            <div className='animate-spin rounded-full h-8 w-8 border-b-2 border-cyan-600'></div>
          </div>
        )}

        {/* Header */}
        <div className='p-4 pb-3'>
          <div className='flex items-start justify-between mb-3'>
            <div className='flex-1 min-w-0'>
              <div className='flex flex-wrap items-center gap-1.5 mb-2'>
                {agent.permissions?.VIEW ? (
                  <h3
                    className='text-base font-bold text-gray-900 dark:text-white truncate max-w-[160px] cursor-pointer hover:text-purple-600 dark:hover:text-purple-400 transition-colors'
                    onClick={() => navigate(`/agent-edit?id=${agent.id}&isReadOnly=true`)}
                  >
                    {agent.name}
                  </h3>
                ) : (
                  <h3 className='text-base font-bold text-gray-900 dark:text-white truncate max-w-[160px]'>
                    {agent.name}
                  </h3>
                )}
              </div>
            </div>

            <div className='flex gap-1'>
              {agent.permissions?.EDIT && (
                <button
                  className='p-1.5 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700/50 rounded-lg transition-all duration-200 flex-shrink-0'
                  onClick={() => toEditPage?.(agent)}
                  title='Edit agent'
                >
                  <PencilIcon className='h-3.5 w-3.5' />
                </button>
              )}
            </div>
          </div>

          {/* Description */}
          <p className='text-gray-600 dark:text-gray-300 text-xs leading-relaxed line-clamp-2 mb-3'>
            {agent.description || 'No description available'}
          </p>

          {/* Tags */}
          {agent.tags && agent.tags.length > 0 && (
            <div className='flex flex-wrap gap-1 mb-3 max-h-10 overflow-hidden'>
              {agent.tags.slice(0, 3).map(tag => (
                <span
                  key={tag}
                  className='px-1.5 py-0.5 text-xs font-medium bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 rounded truncate max-w-[100px]'
                >
                  #{tag}
                </span>
              ))}
              {agent.tags.length > 3 && (
                <span className='px-1.5 py-0.5 text-xs font-medium bg-gray-50 dark:bg-gray-800 text-gray-600 dark:text-gray-300 rounded'>
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
                    className='flex items-center gap-1.5 text-blue-600 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300 hover:bg-blue-50 dark:hover:bg-blue-900/20 px-1.5 py-0.5 -mx-1.5 -my-0.5 rounded transition-all text-xs'
                    title='View skills'
                  >
                    <div className='p-1 bg-blue-50 dark:bg-blue-900/30 rounded'>
                      <WrenchScrewdriverIcon className='h-3.5 w-3.5' />
                    </div>
                    <div>
                      <div className='text-xs font-semibold'>{numSkills}</div>
                      <div className='text-xs'>Skills</div>
                    </div>
                  </button>
                ) : (
                  <div
                    className='flex items-center gap-1.5 text-blue-600 dark:text-blue-400 px-1.5 py-0.5 -mx-1.5 -my-0.5 rounded text-xs'
                    title='Skills count'
                  >
                    <div className='p-1 bg-blue-50 dark:bg-blue-900/30 rounded'>
                      <WrenchScrewdriverIcon className='h-3.5 w-3.5' />
                    </div>
                    <div>
                      <div className='text-xs font-semibold'>{numSkills}</div>
                      <div className='text-xs'>Skills</div>
                    </div>
                  </div>
                )
              ) : (
                <div className='flex items-center gap-1.5 text-gray-400 dark:text-gray-500'>
                  <div className='p-1 bg-gray-50 dark:bg-gray-800 rounded'>
                    <WrenchScrewdriverIcon className='h-3.5 w-3.5' />
                  </div>
                  <div>
                    <div className='text-xs font-semibold'>0</div>
                    <div className='text-xs'>Skills</div>
                  </div>
                </div>
              )}
            </div>
            {hasAgentCoreTags && (
              <div className='flex justify-end items-center'>
                <div
                  className='p-1 bg-gray-50 dark:bg-gray-800 rounded border border-gray-200 dark:border-gray-700 text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-300 transition-colors'
                  title='AWS AgentCore'
                >
                  <FaAws className='h-3.5 w-3.5' />
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className='mt-auto px-3 py-3 border-t border-gray-100 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-900/30 rounded-b-2xl'>
          <div className='flex flex-col sm:flex-row items-center justify-between gap-1'>
            <div className='flex items-center gap-2 flex-wrap justify-center'>
              {/* Status Indicators */}
              <div className='flex items-center gap-1'>
                <div
                  className={`w-2.5 h-2.5 rounded-full ${
                    agent.enabled ? 'bg-green-400 shadow-lg shadow-green-400/30' : 'bg-gray-300 dark:bg-gray-600'
                  }`}
                />
                <span className='text-xs font-medium text-gray-700 dark:text-gray-300'>
                  {agent.enabled ? 'Enabled' : 'Disabled'}
                </span>
              </div>

              <div className='w-px h-3 bg-gray-200 dark:bg-gray-600' />

              <div className='flex items-center gap-1'>
                <div
                  className={`w-2.5 h-2.5 rounded-full ${
                    agent.status === 'active'
                      ? 'bg-emerald-400 shadow-lg shadow-emerald-400/30'
                      : agent.status === 'inactive'
                        ? 'bg-orange-400 shadow-lg shadow-orange-400/30'
                        : agent.status === 'error'
                          ? 'bg-red-400 shadow-lg shadow-red-400/30'
                          : 'bg-amber-400 shadow-lg shadow-amber-400/30'
                  }`}
                />
                <span className='text-xs font-medium text-gray-700 dark:text-gray-300 max-w-[80px] truncate'>
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
                    agent.enabled ? 'bg-blue-600' : 'bg-gray-300 dark:bg-gray-600'
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
        <div className='fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-50'>
          <div className='bg-white dark:bg-gray-800 rounded-xl p-6 pt-0 max-w-2xl w-full mx-4 max-h-[80vh] overflow-auto'>
            <div className='flex items-center justify-between mb-4 sticky top-0 bg-white dark:bg-gray-800 z-10 pb-2 border-b border-gray-100 dark:border-gray-700 -mx-6 px-6 -mt-6 pt-6'>
              <h3 className='text-lg font-semibold text-gray-900 dark:text-white'>Skills for {agent.name}</h3>
              <button
                onClick={() => setShowSkills(false)}
                className='text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200'
              >
                <XMarkIcon className='h-6 w-6' />
              </button>
            </div>

            <div className='space-y-4 mt-[2.8rem]'>
              {(agent as Agent).skills?.length > 0 ? (
                (agent as Agent).skills.map((skill: any, index: number) => (
                  <div key={skill.id || index} className='border border-gray-200 dark:border-gray-700 rounded-lg p-4'>
                    <h4 className='font-medium text-gray-900 dark:text-white mb-2'>{skill.name}</h4>
                    {skill.description && (
                      <p className='text-sm text-gray-600 dark:text-gray-300 mb-2'>{skill.description}</p>
                    )}
                    {skill.tags && skill.tags.length > 0 && (
                      <div className='flex flex-wrap gap-1 mb-2'>
                        {skill.tags.map((tag: any) => (
                          <span
                            key={tag}
                            className='px-1.5 py-0.5 text-xs font-medium bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 rounded'
                          >
                            #{tag}
                          </span>
                        ))}
                      </div>
                    )}
                    {(skill.inputModes?.length > 0 || skill.outputModes?.length > 0) && (
                      <details className='text-xs'>
                        <summary className='cursor-pointer text-gray-500 dark:text-gray-300'>View Modes</summary>
                        <div className='mt-2 p-3 bg-gray-50 dark:bg-gray-900 border dark:border-gray-700 rounded text-gray-900 dark:text-gray-100 space-y-1'>
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
                            <div className='text-xs font-semibold text-gray-700 dark:text-gray-300 flex items-center gap-1'>
                              <span className='w-1 h-3 bg-blue-500 rounded-full'></span>
                              Examples
                            </div>
                            <div className='space-y-3'>
                              {skill.examples.map((example: any, i: number) => {
                                const parsedExample = parseSkillExample(example);

                                if (parsedExample.prettyJson) {
                                  return (
                                    <div
                                      key={i}
                                      className='rounded-lg border border-gray-100 dark:border-gray-800 overflow-hidden'
                                    >
                                      {parsedExample.label && (
                                        <div className='px-2 py-1 bg-gray-50 dark:bg-gray-900/50 border-b border-gray-100 dark:border-gray-800 text-[10px] font-bold uppercase tracking-wider text-gray-500 dark:text-gray-400'>
                                          {parsedExample.label}
                                        </div>
                                      )}
                                      <pre className='p-2.5 bg-gray-50/50 dark:bg-gray-900/30 text-[11px] font-mono text-blue-600 dark:text-blue-400 overflow-x-auto'>
                                        {parsedExample.prettyJson}
                                      </pre>
                                    </div>
                                  );
                                }

                                return (
                                  <div
                                    key={i}
                                    className='p-2.5 bg-gray-50/50 dark:bg-gray-900/30 rounded-lg border border-gray-100 dark:border-gray-800 text-[11px] text-gray-600 dark:text-gray-400 italic break-all'
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
                <p className='text-gray-500 dark:text-gray-300'>No skills available for this agent.</p>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
};

export default AgentCard;
