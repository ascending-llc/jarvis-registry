import { CheckIcon, PlayIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { useSearchParams } from 'react-router-dom';
import WorkflowCanvas from '@/components/WorkflowCanvas';

const WorkflowRegistryOrEdit: React.FC = () => {
  const [searchParams] = useSearchParams();
  const id = searchParams.get('id');

  /* Title: use workflow name from URL if editing, else "New Workflow" */
  const title = id ? (searchParams.get('name') ?? `Workflow #${id}`) : 'New Workflow';

  return (
    // Negative margins cancel out Layout's px-4 sm:px-6 lg:px-8 pt-4 md:pt-8 pb-1 md:pb-2
    <div
      className='-mx-4 sm:-mx-6 lg:-mx-8 -mt-4 md:-mt-8 -mb-1 md:-mb-2'
      style={{ height: 'calc(100% + 2.25rem)', display: 'flex', flexDirection: 'column' }}
    >
      {/* ── Page Header ── */}
      <div
        className='flex items-center justify-between px-5 border-b border-[color:var(--jarvis-border)] bg-[var(--jarvis-surface)]'
        style={{ height: 48, flexShrink: 0 }}
      >
        {/* Left: workflow title */}
        <span className='text-sm font-semibold text-[var(--jarvis-text-strong)] tracking-tight'>{title}</span>

        {/* Right: action buttons */}
        <div className='flex items-center gap-2'>
          {/* Trigger run */}
          <button className='inline-flex items-center gap-1 px-2.5 py-1 border border-transparent rounded-md text-xs font-medium text-white bg-green-600 hover:bg-green-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-green-500 disabled:opacity-50 disabled:cursor-not-allowed'>
            <PlayIcon className='h-3.5 w-3.5' />
            Trigger run
          </button>

          {/* Save */}
          <button className='inline-flex items-center justify-center gap-1 px-2.5 py-1 border border-transparent rounded-md text-xs font-medium text-white bg-[var(--jarvis-primary-hover)] hover:opacity-90 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-[var(--jarvis-primary)] disabled:opacity-50 disabled:cursor-not-allowed'>
            <CheckIcon className='h-3.5 w-3.5' />
            Save
          </button>
        </div>
      </div>

      {/* ── Canvas (fills remaining height) ── */}
      <div style={{ flex: 1, overflow: 'hidden' }}>
        <WorkflowCanvas />
      </div>
    </div>
  );
};

export default WorkflowRegistryOrEdit;
