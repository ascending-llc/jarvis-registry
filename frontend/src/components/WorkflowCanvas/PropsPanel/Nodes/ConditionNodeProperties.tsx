import type { Node } from '@xyflow/react';
import type React from 'react';
import { useUpstreamSchema } from '../../hooks/useUpstreamSchema';
import type { CondNodeData } from '../../types';
import { CELContextReference } from '../CELContextReference';
import { useWorkflowPanel } from '../WorkflowPanelContext';

interface Props {
  node: Node<CondNodeData>;
}

export const ConditionNodeProperties: React.FC<Props> = ({ node }) => {
  const { nodes, edges, agentSchemas, onNodeDataChange } = useWorkflowPanel();
  const { upstreamSchema, sourceLabel } = useUpstreamSchema(node, nodes, edges, agentSchemas);

  const nodeData = node.data;

  const outEdges = edges.filter(e => e.source === node.id);
  const trueEdge = outEdges.find(e => e.sourceHandle === 'true');
  const falseEdge = outEdges.find(e => e.sourceHandle === 'false');
  const trueNode = trueEdge ? nodes.find(n => n.id === trueEdge.target) : null;
  const falseNode = falseEdge ? nodes.find(n => n.id === falseEdge.target) : null;
  const isAdd = (n: Node | null | undefined): boolean => n?.type === 'add';

  interface BranchSlotProps {
    label: string;
    color: string;
    targetNode: Node | null | undefined;
    icon: string;
  }

  const BranchSlot = ({ label, color, targetNode, icon }: BranchSlotProps) => (
    <div className='flex flex-col gap-1'>
      <div className='flex items-center gap-1.5'>
        <span className='text-[9px] font-mono font-bold tracking-wide uppercase' style={{ color }}>
          {label}
        </span>
        <span className='text-[9px]' style={{ color }}>
          {icon}
        </span>
      </div>
      {targetNode && !isAdd(targetNode) ? (
        <div
          className='flex items-center gap-1.5 bg-[var(--jarvis-card-muted)] rounded-md px-2 py-1.5'
          style={{ border: `1px solid ${color}33` }}
        >
          <div
            className='w-[22px] h-[22px] rounded flex items-center justify-center font-mono font-bold text-[8px] shrink-0'
            style={{ background: `${color}22`, color }}
          >
            {((targetNode.data?.label as string) || '')
              .split(' ')
              .map((w: string) => w[0])
              .join('')
              .slice(0, 2)
              .toUpperCase()}
          </div>
          <div className='flex-1 min-w-0'>
            <div className='text-[11.5px] font-medium text-[var(--jarvis-text-strong)] truncate'>
              {targetNode.data?.label as string}
            </div>
            <div className='text-[9px] text-[var(--jarvis-subtle)] mt-0.5'>{targetNode.type ?? 'unknown'} step</div>
          </div>
        </div>
      ) : (
        <div className='text-[11px] text-[var(--jarvis-subtle)] italic bg-[var(--jarvis-card-muted)] border border-dashed border-[var(--jarvis-border)] rounded-md px-2 py-1.5'>
          Not connected — draw an edge from the canvas
        </div>
      )}
    </div>
  );

  return (
    <div className='px-4 py-3 border-b border-[var(--jarvis-border)]'>
      <div className='font-mono text-[10px] font-bold tracking-wide uppercase text-[var(--jarvis-subtle)] mb-2'>
        If / Else
      </div>
      <CELContextReference upstreamSchema={upstreamSchema} sourceLabel={sourceLabel} />
      <div className='mb-2'>
        <label className='block text-xs text-[var(--jarvis-muted)] mb-1'>If — condition (CEL)</label>
        <input
          className='w-full bg-[var(--jarvis-card-muted)] border border-[var(--jarvis-border)] rounded-md text-[var(--jarvis-text-strong)] font-mono text-[11px] px-2 py-1.5 outline-none'
          value={nodeData.expression ?? 'session_state.score > 0.8'}
          onChange={e => onNodeDataChange(node.id, { expression: e.target.value })}
        />
      </div>
      <div className='grid grid-cols-2 gap-2 mt-1'>
        <BranchSlot label='If true' color='var(--jarvis-blue)' targetNode={trueNode} icon='→' />
        <BranchSlot label='If false' color='var(--jarvis-subtle)' targetNode={falseNode} icon='↓' />
      </div>
      <p className='text-[11px] text-[var(--jarvis-subtle)] mt-2 leading-relaxed'>
        Connect nodes from the canvas — upper right handle for true, lower right handle for false.
      </p>
    </div>
  );
};
