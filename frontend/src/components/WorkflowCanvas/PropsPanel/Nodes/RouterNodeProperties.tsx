import type { Node } from '@xyflow/react';
import type React from 'react';
import { useUpstreamSchema } from '../../hooks/useUpstreamSchema';
import type { RouterNodeData } from '../../types';
import { CELContextReference } from '../CELContextReference';
import { BranchList } from '../shared';
import { useWorkflowPanel } from '../WorkflowPanelContext';

interface Props {
  node: Node<RouterNodeData>;
}

export const RouterNodeProperties: React.FC<Props> = ({ node }) => {
  const { nodes, edges, agentSchemas, onNodeDataChange, onRouterCasesChange } = useWorkflowPanel();
  const { upstreamSchema, sourceLabel } = useUpstreamSchema(node, nodes, edges, agentSchemas);

  const nodeData = node.data;
  const routerCases = Array.isArray(nodeData.cases) ? nodeData.cases : ['critical', 'normal'];

  return (
    <div className='px-4 py-3 border-b border-[var(--jarvis-border)]'>
      <div className='font-mono text-[10px] font-bold tracking-wide uppercase text-[var(--jarvis-subtle)] mb-2'>
        Switch / case
      </div>
      <CELContextReference upstreamSchema={upstreamSchema} sourceLabel={sourceLabel} />
      <div className='mb-2'>
        <label className='block text-xs text-[var(--jarvis-muted)] mb-1'>Route by (CEL)</label>
        <input
          className='w-full bg-[var(--jarvis-card-muted)] border border-[var(--jarvis-border)] rounded-md text-[var(--jarvis-text-strong)] font-mono text-[11px] px-2 py-1.5 outline-none'
          value={nodeData.routeBy ?? 'session_state.severity'}
          onChange={e => onNodeDataChange(node.id, { routeBy: e.target.value })}
        />
      </div>
      <div className='mb-1.5'>
        <div className='text-xs text-[var(--jarvis-muted)] mb-1'>Cases</div>
        <BranchList
          items={routerCases}
          onAdd={() => onRouterCasesChange(node.id, routerCases, [...routerCases, ''])}
          onRm={i =>
            routerCases.length > 1 && onRouterCasesChange(node.id, routerCases, routerCases.filter((_, j) => j !== i))
          }
          onChange={(i, val) => {
            const next = [...routerCases];
            next[i] = val;
            onNodeDataChange(node.id, { cases: next });
          }}
          addLabel='+ Add case'
          prefix='case'
        />
      </div>
      <div className='mb-2'>
        <label className='block text-xs text-[var(--jarvis-muted)] mb-1'>Default (fallthrough)</label>
        <input
          className='w-full bg-[var(--jarvis-card-muted)] border border-[var(--jarvis-border)] rounded-md text-[var(--jarvis-text-strong)] font-sans text-[11.5px] px-2 py-1.5 outline-none'
          value={nodeData.defaultCase ?? 'low-priority'}
          onChange={e => onNodeDataChange(node.id, { defaultCase: e.target.value })}
        />
      </div>
    </div>
  );
};
