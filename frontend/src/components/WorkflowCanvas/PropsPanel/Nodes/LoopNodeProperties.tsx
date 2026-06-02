import type { Node } from '@xyflow/react';
import type React from 'react';
import { useUpstreamSchema } from '../../hooks/useUpstreamSchema';
import type { LoopNodeData } from '../../types';
import { CELContextReference } from '../CELContextReference';
import { AddButton } from '../shared';
import { useWorkflowPanel } from '../WorkflowPanelContext';

interface Props {
  node: Node<LoopNodeData>;
}

export const LoopNodeProperties: React.FC<Props> = ({ node }) => {
  const { nodes, edges, agentSchemas, onNodeDataChange, onOpenAgentPicker } = useWorkflowPanel();
  const { upstreamSchema, sourceLabel } = useUpstreamSchema(node, nodes, edges, agentSchemas);

  const nodeData = node.data;
  // Fix the local state bug: persist agent selection in nodeData.agents (max 1 for loop)
  const loopAgent = nodeData.agents?.[0] ?? null;

  return (
    <div className='px-4 py-3 border-b border-[var(--jarvis-border)]'>
      <div className='font-mono text-[10px] font-bold tracking-wide uppercase text-[var(--jarvis-subtle)] mb-2'>
        Loop config
      </div>
      <CELContextReference upstreamSchema={upstreamSchema} sourceLabel={sourceLabel} />
      <div className='mb-2'>
        <div className='text-xs text-[var(--jarvis-muted)] mb-1'>Agent (runs each iteration)</div>
        {loopAgent ? (
          <div className='flex items-center gap-2 bg-[var(--jarvis-card-muted)] border border-[var(--jarvis-border)] rounded-md px-2 py-1.5'>
            <div className='w-6 h-6 rounded flex items-center justify-center font-mono font-bold text-[9px] shrink-0 bg-[var(--jarvis-primary-soft)] text-[var(--jarvis-primary-text)]'>
              {loopAgent.id
                .split('-')
                .map(w => w[0].toUpperCase())
                .join('')
                .slice(0, 2)}
            </div>
            <div className='flex-1 min-w-0'>
              <div className='text-xs font-medium text-[var(--jarvis-text-strong)]'>{loopAgent.label}</div>
              <div className='text-[10px] text-[var(--jarvis-subtle)]'>{loopAgent.desc}</div>
            </div>
            <button
              className='shrink-0 rounded p-0.5 transition-colors hover:bg-[var(--jarvis-danger-soft)] hover:text-[var(--jarvis-danger-text)] bg-none border-none text-[var(--jarvis-subtle)] cursor-pointer text-[13px]'
              onClick={() => onNodeDataChange(node.id, { agents: [] })}
            >
              ×
            </button>
          </div>
        ) : (
          <AddButton
            onClick={() => {
              onOpenAgentPicker(agent => {
                onNodeDataChange(node.id, { agents: [agent] });
              });
            }}
          >
            + Select agent from registry
          </AddButton>
        )}
      </div>
      <div className='mb-2'>
        <label className='block text-xs text-[var(--jarvis-muted)] mb-1'>Max iterations</label>
        <input
          type='number'
          className='[appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none w-20 bg-[var(--jarvis-card-muted)] border border-[var(--jarvis-border)] rounded-md text-[var(--jarvis-text-strong)] font-sans text-xs px-2 py-1.5 outline-none'
          value={nodeData.maxIterations ?? 5}
          onChange={e => onNodeDataChange(node.id, { maxIterations: parseInt(e.target.value, 10) || 1 })}
          min={1}
        />
      </div>
      <div className='mb-2'>
        <label className='block text-xs text-[var(--jarvis-muted)] mb-1'>Exit when (CEL)</label>
        <input
          className='w-full bg-[var(--jarvis-card-muted)] border border-[var(--jarvis-border)] rounded-md text-[var(--jarvis-text-strong)] font-mono text-[11px] px-2 py-1.5 outline-none'
          value={nodeData.exitCondition ?? 'session_state.done == true'}
          onChange={e => onNodeDataChange(node.id, { exitCondition: e.target.value })}
        />
      </div>
      <p className='text-[11px] text-[var(--jarvis-subtle)] leading-relaxed'>
        The selected agent runs on each iteration until the exit condition or max iterations is reached.
      </p>
    </div>
  );
};
