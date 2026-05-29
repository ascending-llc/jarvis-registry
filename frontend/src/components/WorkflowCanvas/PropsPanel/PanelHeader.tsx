import type React from 'react';

interface PanelHeaderProps {
  iconText: string;
  label: string;
  scope: string;
  isWorkflow: boolean;
}

export const PanelHeader: React.FC<PanelHeaderProps> = ({ iconText, label, scope, isWorkflow }) => {
  return (
    <div className='flex items-center gap-2 px-3.5 py-2.5 border-b border-[var(--jarvis-border)] bg-[var(--jarvis-card-muted)] shrink-0'>
      <div
        className='w-[22px] h-[22px] rounded-md flex items-center justify-center text-[10px] font-bold shrink-0'
        style={{
          background: isWorkflow ? 'var(--jarvis-primary-soft)' : 'var(--jarvis-card)',
          color: isWorkflow ? 'var(--jarvis-primary)' : 'var(--jarvis-muted)',
        }}
      >
        {iconText}
      </div>
      <div className='text-xs font-semibold text-[var(--jarvis-text-strong)] flex-1 whitespace-nowrap overflow-hidden text-ellipsis'>
        {label}
      </div>
      <div className='text-[10px] font-mono text-[var(--jarvis-subtle)] shrink-0'>{scope}</div>
    </div>
  );
};

export default PanelHeader;
