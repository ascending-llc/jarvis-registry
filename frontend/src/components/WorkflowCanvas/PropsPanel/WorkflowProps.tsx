import { TrashIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { useWorkflowPanel } from './WorkflowPanelContext';

interface WorkflowPropsProps {
  isReadOnly: boolean;
  isNewWorkflow: boolean;
}

const WorkflowProps: React.FC<WorkflowPropsProps> = ({ isReadOnly, isNewWorkflow }) => {
  const { workflow, onWorkflowChange: onChange, onDeleteWorkflow } = useWorkflowPanel();
  if (!workflow) return null;

  return (
    <>
      <div className='px-4 py-3 border-b border-[var(--jarvis-border)]'>
        <div className='font-mono text-[10px] font-bold tracking-wide uppercase text-[var(--jarvis-subtle)] mb-2'>
          Workflow
        </div>

        <div className='mb-3'>
          <label className='block text-xs text-[var(--jarvis-muted)] mb-1'>Title</label>
          <input
            className='w-full bg-[var(--jarvis-card-muted)] border border-[var(--jarvis-border)] rounded-md text-[var(--jarvis-text-strong)] font-sans text-xs px-2 py-1.5 outline-none focus:ring-2 focus:ring-[var(--jarvis-primary)] disabled:opacity-60 disabled:cursor-not-allowed'
            value={workflow.name}
            onChange={e => onChange({ name: e.target.value })}
            disabled={isReadOnly}
          />
        </div>

        <div className='mb-3'>
          <label className='block text-xs text-[var(--jarvis-muted)] mb-1'>Description</label>
          <textarea
            className='w-full bg-[var(--jarvis-card-muted)] border border-[var(--jarvis-border)] rounded-md text-[var(--jarvis-text-strong)] font-sans text-xs px-2 py-1.5 outline-none focus:ring-2 focus:ring-[var(--jarvis-primary)] resize-none disabled:opacity-60 disabled:cursor-not-allowed'
            rows={3}
            value={workflow.description ?? ''}
            onChange={e => onChange({ description: e.target.value })}
            disabled={isReadOnly}
            placeholder='Add a description for this workflow'
          />
        </div>
      </div>

      <div className='px-4 py-3 border-t border-[var(--jarvis-border)] shrink-0'>
        <button
          type='button'
          onClick={onDeleteWorkflow}
          disabled={isReadOnly || isNewWorkflow}
          className='w-full inline-flex items-center justify-center gap-2 px-4 py-2 border border-[var(--jarvis-border)] rounded-md shadow-sm text-sm font-medium text-[var(--jarvis-danger-text)] bg-[var(--jarvis-card)] hover:bg-[var(--jarvis-danger-soft)] focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-[var(--jarvis-danger)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed'
        >
          <TrashIcon className='h-4 w-4' />
          Delete workflow
        </button>
        {isNewWorkflow && (
          <p className='text-[10px] text-[var(--jarvis-subtle)] mt-1 text-center'>Save workflow before deleting</p>
        )}
      </div>
    </>
  );
};

export default WorkflowProps;
