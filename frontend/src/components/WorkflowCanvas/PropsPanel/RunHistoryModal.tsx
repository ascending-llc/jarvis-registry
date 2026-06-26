import { DocumentDuplicateIcon, PlayIcon, XMarkIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { useEffect, useMemo, useState } from 'react';
import IconButton from '@/components/IconButton';
import { useGlobal } from '@/contexts/GlobalContext';
import type { RunEntry } from '../types';

interface RunHistoryModalProps {
  isOpen: boolean;
  runEntry: RunEntry | null;
  onClose: () => void;
  onReplay: (runEntry: RunEntry) => void;
  replaying: boolean;
}

const formatBytes = (obj: any) => {
  if (obj === undefined || obj === null) return '0 B';
  const l = JSON.stringify(obj).length;
  return l < 1024 ? `${l} B` : `${(l / 1024).toFixed(1)} KB`;
};

const syntaxHighlight = (obj: any) => {
  if (obj === undefined || obj === null) return '';
  return JSON.stringify(obj, null, 2)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"([^"]+)"(\s*:)/g, '<span class="text-[#a78bfa]">"$1"</span>$2') // keys
    .replace(/: "([^"]*)"/g, (_m, v) => `: <span class="text-[#4ade80]">"${v}"</span>`) // strings
    .replace(/: (-?\d+\.?\d*)/g, (_m, v) => `: <span class="text-[#f59e0b]">${v}</span>`) // numbers
    .replace(/: (true|false)/g, (_m, v) => `: <span class="text-[#38bdf8]">${v}</span>`); // booleans
};

const RunHistoryModal: React.FC<RunHistoryModalProps> = ({ isOpen, runEntry, onClose, onReplay, replaying }) => {
  const { showToast } = useGlobal();
  const [activeTab, setActiveTab] = useState<'in' | 'out'>('in');

  useEffect(() => {
    if (isOpen) {
      setActiveTab('in');
    }
  }, [isOpen, runEntry]);

  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  const { codeHtml, size } = useMemo(() => {
    const data = activeTab === 'in' ? runEntry?.input : runEntry?.output;
    return {
      codeHtml: syntaxHighlight(data),
      size: formatBytes(data),
    };
  }, [activeTab, runEntry]);

  const handleCopy = async () => {
    const data = activeTab === 'in' ? runEntry?.input : runEntry?.output;
    if (!data) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(data, null, 2));
      showToast('Copied to clipboard', 'success');
    } catch {
      showToast('Failed to copy', 'error');
    }
  };

  const handleReplay = () => {
    if (runEntry) {
      onReplay(runEntry);
    }
  };

  if (!isOpen || !runEntry) return null;

  const isError = runEntry.err !== undefined || (activeTab === 'out' && runEntry.status === 'fail');
  const typeLabel = runEntry.type === 'workflow' ? 'WF' : 'NODE';
  const headerIconBg = runEntry.type === 'workflow' ? 'bg-[#0e7490]' : 'bg-[var(--jarvis-primary)]';

  return (
    <div
      className='fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4 py-6 backdrop-blur-sm'
      onClick={replaying ? undefined : onClose}
    >
      <div
        className='relative w-full max-w-[560px] rounded-xl border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] shadow-2xl overflow-hidden flex flex-col'
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className='flex items-center justify-between px-5 py-4 border-b border-[color:var(--jarvis-border-soft)]'>
          <div className='flex items-center gap-2.5'>
            <div
              className={`flex h-7 w-7 items-center justify-center rounded-md text-[10px] font-bold tracking-wider text-white shadow-sm ${headerIconBg}`}
            >
              {typeLabel}
            </div>
            <div>
              <div className='text-[15px] font-medium text-[var(--jarvis-text-strong)]'>
                Run history — {runEntry.nodeName || 'Workflow'} — {runEntry.id}
              </div>
              <div className='text-[11px] text-[var(--jarvis-subtle)] mt-0.5'>
                {runEntry.time} {runEntry.dur ? `· ${runEntry.dur}` : ''}
              </div>
            </div>
          </div>
          <IconButton
            ariaLabel='Close modal'
            tooltip='Close'
            onClick={onClose}
            size='card'
            className='text-[var(--jarvis-muted)] hover:text-[var(--jarvis-text)]'
          >
            <XMarkIcon className='h-4 w-4' />
          </IconButton>
        </div>

        {/* Tabs */}
        <div className='flex border-b border-[color:var(--jarvis-border-soft)] px-5'>
          <button
            type='button'
            onClick={() => setActiveTab('in')}
            className={`flex items-center gap-1.5 border-b-2 px-1 py-2.5 mr-6 text-[13px] transition-colors ${
              activeTab === 'in'
                ? 'border-[var(--jarvis-primary)] text-[var(--jarvis-primary-text)]'
                : 'border-transparent text-[var(--jarvis-subtle)] hover:text-[var(--jarvis-text)]'
            }`}
          >
            <span className='h-1.5 w-1.5 rounded-full bg-[#38bdf8]' /> Input
          </button>
          <button
            type='button'
            onClick={() => setActiveTab('out')}
            className={`flex items-center gap-1.5 border-b-2 px-1 py-2.5 text-[13px] transition-colors ${
              activeTab === 'out'
                ? 'border-[var(--jarvis-primary)] text-[var(--jarvis-primary-text)]'
                : 'border-transparent text-[var(--jarvis-subtle)] hover:text-[var(--jarvis-text)]'
            }`}
          >
            <span className={`h-1.5 w-1.5 rounded-full ${isError ? 'bg-[#f87171]' : 'bg-[#4ade80]'}`} />{' '}
            {isError ? 'Error' : 'Output'}
          </button>
        </div>

        {/* Code Pane */}
        <div className='p-5'>
          <div className='max-h-[280px] overflow-auto rounded-lg border border-[color:var(--jarvis-border)] bg-[#060d1a] p-3.5'>
            <pre
              className='font-mono text-[12px] leading-[1.75] text-[#94a3b8] whitespace-pre-wrap break-all'
              dangerouslySetInnerHTML={{ __html: codeHtml || 'No data' }}
            />
          </div>
          <div className='mt-2 flex items-center justify-between'>
            <button
              type='button'
              onClick={handleCopy}
              className='flex items-center gap-1 rounded border border-[color:var(--jarvis-border)] bg-transparent px-2.5 py-1 text-[11px] text-[var(--jarvis-subtle)] transition-colors hover:border-[var(--jarvis-primary-soft)] hover:text-[var(--jarvis-text)]'
            >
              <DocumentDuplicateIcon className='h-3.5 w-3.5' /> Copy
            </button>
            <span className='font-mono text-[10px] text-[var(--jarvis-faint)]'>{size}</span>
          </div>
        </div>

        {/* Footer */}
        <div className='flex items-center justify-end gap-2 border-t border-[color:var(--jarvis-border-soft)] bg-[var(--jarvis-card)] px-5 py-3.5'>
          <button
            type='button'
            onClick={onClose}
            disabled={replaying}
            className='rounded-md border border-[color:var(--jarvis-border)] bg-transparent px-4 py-1.5 text-[13px] text-[var(--jarvis-subtle)] transition-colors hover:border-[var(--jarvis-border-strong)] hover:text-[var(--jarvis-text)] disabled:cursor-not-allowed disabled:opacity-50'
          >
            Close
          </button>
          <button
            type='button'
            onClick={handleReplay}
            disabled={replaying}
            className='flex items-center gap-1.5 rounded-md border border-transparent bg-[var(--jarvis-primary)] px-4 py-1.5 text-[13px] font-medium text-white transition-colors hover:bg-[var(--jarvis-primary-hover)] disabled:cursor-not-allowed disabled:bg-[var(--jarvis-primary-muted)] disabled:text-[var(--jarvis-subtle)]'
          >
            {replaying ? (
              <svg className='h-4 w-4 animate-spin' viewBox='0 0 24 24' fill='none'>
                <circle className='opacity-25' cx='12' cy='12' r='10' stroke='currentColor' strokeWidth='4' />
                <path
                  className='opacity-75'
                  fill='currentColor'
                  d='M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z'
                />
              </svg>
            ) : (
              <PlayIcon className='h-4 w-4' />
            )}
            Replay
          </button>
        </div>
      </div>
    </div>
  );
};

export default RunHistoryModal;
