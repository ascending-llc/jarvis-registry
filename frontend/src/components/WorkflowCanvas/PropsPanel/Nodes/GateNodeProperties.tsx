import type { Node } from '@xyflow/react';
import type React from 'react';
import { SelectField } from '@/components/FormFields/SelectField';
import type { GateNodeData } from '../../types';
import { useWorkflowPanel } from '../WorkflowPanelContext';

interface Props {
  node: Node<GateNodeData>;
}

export const GateNodeProperties: React.FC<Props> = ({ node }) => {
  const { isReadOnly, onNodeDataChange } = useWorkflowPanel();
  const nodeData = node.data;

  return (
    <div className='px-4 py-3 border-b border-[var(--jarvis-border)]'>
      <div className='font-mono text-[10px] font-bold tracking-wide uppercase text-[var(--jarvis-subtle)] mb-2'>
        Human-in-the-loop
      </div>
      <div className='bg-[rgba(245,158,11,.06)] border border-[rgba(245,158,11,.22)] rounded-md p-3'>
        <div className='font-mono text-[10px] font-bold tracking-wide text-[var(--jarvis-warning)] mb-2'>
          ⏸ APPROVAL GATE
        </div>
        <div className='mb-2'>
          <label className='block text-xs text-[var(--jarvis-muted)] mb-1'>Reviewer prompt</label>
          <textarea
            className='resize-none h-[52px] leading-snug w-full bg-[var(--jarvis-card-muted)] border border-[var(--jarvis-border)] rounded-md text-[var(--jarvis-text-strong)] font-sans text-xs px-2 py-1.5 outline-none'
            value={nodeData.reviewerPrompt ?? 'Review and approve to proceed, or reject to cancel.'}
            onChange={e => onNodeDataChange(node.id, { reviewerPrompt: e.target.value })}
            disabled={isReadOnly}
          />
        </div>
        <div className='mb-2'>
          <SelectField
            label='Timeout'
            options={[
              { value: '24h', label: '24 hours' },
              { value: '4h', label: '4 hours' },
              { value: 'none', label: 'No timeout' },
            ]}
            value={nodeData.timeout ?? '24h'}
            onChange={val => onNodeDataChange(node.id, { timeout: val })}
            disabled={isReadOnly}
          />
        </div>
        <div className='mb-2'>
          <SelectField
            label='On timeout'
            options={[
              { value: 'cancel', label: 'Auto-cancel' },
              { value: 'skip', label: 'Auto-skip' },
              { value: 'approve', label: 'Auto-approve' },
            ]}
            value={nodeData.onTimeout ?? 'cancel'}
            onChange={val => onNodeDataChange(node.id, { onTimeout: val })}
            disabled={isReadOnly}
          />
        </div>
      </div>
    </div>
  );
};
