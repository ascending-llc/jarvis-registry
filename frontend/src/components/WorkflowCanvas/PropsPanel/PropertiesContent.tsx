import { TrashIcon } from '@heroicons/react/24/outline';
import type { Node } from '@xyflow/react';
import type React from 'react';
import type { NodeData, PanelMode } from '../types';
import { ConditionNodeProperties } from './Nodes/ConditionNodeProperties';
import { GateNodeProperties } from './Nodes/GateNodeProperties';
import { LoopNodeProperties } from './Nodes/LoopNodeProperties';
import { ParallelNodeProperties } from './Nodes/ParallelNodeProperties';
import { PoolNodeProperties } from './Nodes/PoolNodeProperties';
import { RouterNodeProperties } from './Nodes/RouterNodeProperties';
import { useWorkflowPanel } from './WorkflowPanelContext';
import WorkflowProps from './WorkflowProps';

interface PropertiesContentProps {
  panelMode: PanelMode;
  isReadOnly: boolean;
  isNewWorkflow: boolean;
}

const PropertiesEmptyState: React.FC<{ message: string }> = ({ message }) => (
  <div className='flex flex-col items-center justify-center min-h-[200px] gap-2.5 p-7'>
    <div className='w-10 h-10 rounded-lg bg-[var(--jarvis-card-muted)] border border-[var(--jarvis-border)] flex items-center justify-center'>
      <svg fill='none' stroke='currentColor' viewBox='0 0 24 24' width='17' height='17'>
        <path strokeLinecap='round' strokeLinejoin='round' strokeWidth='1.5' d='M4 5h16M4 12h10M4 19h6' />
      </svg>
    </div>
    <p className='text-xs text-[var(--jarvis-subtle)] text-center leading-relaxed'>{message}</p>
  </div>
);

export const PropertiesContent: React.FC<PropertiesContentProps> = ({ panelMode, isReadOnly, isNewWorkflow }) => {
  const { workflow, selectedNode, onNodeDataChange, onDeleteNode } = useWorkflowPanel();

  if (panelMode === 'workflow') {
    if (workflow) {
      return <WorkflowProps isReadOnly={isReadOnly} isNewWorkflow={isNewWorkflow} />;
    }
    return <PropertiesEmptyState message='Save workflow to view properties' />;
  }

  if (!selectedNode) {
    return <PropertiesEmptyState message='Click a node to edit its properties' />;
  }

  const nodeData = selectedNode.data as NodeData | undefined;
  const nodeType = selectedNode.type ?? '';

  return (
    <>
      <div className='px-4 py-3 border-b border-[var(--jarvis-border)]'>
        <div className='font-mono text-[10px] font-bold tracking-wide uppercase text-[var(--jarvis-subtle)] mb-2'>
          Node
        </div>
        <div className='mb-2'>
          <label className='block text-xs text-[var(--jarvis-muted)] mb-1'>Title</label>
          <input
            className='w-full bg-[var(--jarvis-card-muted)] border border-[var(--jarvis-border)] rounded-md text-[var(--jarvis-text-strong)] font-sans text-xs px-2 py-1.5 outline-none focus:ring-2 focus:ring-[var(--jarvis-primary)]'
            value={(nodeData?.label as string) || ''}
            onChange={e => onNodeDataChange(selectedNode.id, { label: e.target.value })}
          />
        </div>
      </div>

      {nodeType === 'gate' && <GateNodeProperties node={selectedNode as Node<any>} />}
      {nodeType === 'cond' && <ConditionNodeProperties node={selectedNode as Node<any>} />}
      {nodeType === 'router' && <RouterNodeProperties node={selectedNode as Node<any>} />}
      {nodeType === 'loop' && <LoopNodeProperties node={selectedNode as Node<any>} />}
      {nodeType === 'parallel' && <ParallelNodeProperties node={selectedNode as Node<any>} />}
      {nodeType === 'pool' && <PoolNodeProperties node={selectedNode as Node<any>} />}

      {/* Delete Node Button */}
      <div className='px-4 py-3 border-t border-[var(--jarvis-border)] shrink-0'>
        <button
          type='button'
          onClick={() => onDeleteNode?.(selectedNode.id)}
          className='w-full inline-flex items-center justify-center gap-2 px-4 py-2 border border-[var(--jarvis-border)] rounded-md shadow-sm text-sm font-medium text-[var(--jarvis-danger-text)] bg-[var(--jarvis-card)] hover:bg-[var(--jarvis-danger-soft)] focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-[var(--jarvis-danger)] transition-colors'
        >
          <TrashIcon className='h-4 w-4' />
          Delete node
        </button>
      </div>
    </>
  );
};

export default PropertiesContent;
