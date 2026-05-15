import type React from 'react';
import type { AgentInfo, NodeData, PoolPropsProps } from '../types';
import { AddButton } from './shared';

/** PoolProps - handles Agent Pool node configuration (up to 5 delegate agents). */
const PoolProps: React.FC<PoolPropsProps> = ({ node, onNodeDataChange, onOpenAgentPicker }) => {
  const nodeData = node.data as NodeData | undefined;
  const poolAgents = nodeData?.agents ?? [];

  const onDataChange = (agents: AgentInfo[]) => {
    onNodeDataChange(node.id, { agents });
  };

  return (
    <div className='px-4 py-3 border-b border-[var(--jarvis-border)]'>
      <div className='font-mono text-[10px] font-bold tracking-wide uppercase text-[var(--jarvis-subtle)] mb-2'>
        Delegate agents <span className='text-[var(--jarvis-subtle)] font-normal'>({poolAgents.length} / 5)</span>
      </div>
      <div>
        {poolAgents.map((a, i) => (
          <div
            key={a.id + i}
            className='flex items-center gap-2 bg-[var(--jarvis-card-muted)] border border-[var(--jarvis-border)] rounded-md px-2 py-1.5 mb-1'
          >
            <div className='w-6 h-6 rounded flex items-center justify-center font-mono font-bold text-[9px] shrink-0 bg-[var(--jarvis-primary-soft)] text-[var(--jarvis-primary-hover)]'>
              {a.id
                .split('-')
                .map(w => w[0].toUpperCase())
                .join('')
                .slice(0, 2)}
            </div>
            <div className='flex-1 min-w-0'>
              <div className='text-xs font-medium text-[var(--jarvis-text-strong)]'>{a.label}</div>
              <div className='text-[10px] text-[var(--jarvis-subtle)]'>{a.desc}</div>
            </div>
            <button
              className='shrink-0 rounded p-0.5 transition-colors hover:bg-[var(--jarvis-danger-soft)] hover:text-[var(--jarvis-danger-text)] bg-none border-none text-[var(--jarvis-subtle)] cursor-pointer text-[13px]'
              onClick={() => onDataChange(poolAgents.filter((_, j) => j !== i))}
            >
              ×
            </button>
          </div>
        ))}
        {poolAgents.length < 5 && (
          <AddButton
            onClick={() =>
              onOpenAgentPicker(agent => {
                if (!poolAgents.find(a => a.id === agent.id)) {
                  onDataChange([...poolAgents, agent]);
                }
              })
            }
          >
            + Add agent from registry
          </AddButton>
        )}
      </div>
      <p className='text-[11px] text-[var(--jarvis-subtle)] leading-relaxed'>
        The LLM selects the best-fit agent at runtime. All agents share a single output edge.
      </p>
    </div>
  );
};

export { PoolProps };
export default PoolProps;
