import { CodeBracketIcon, PlayIcon, TrashIcon, XMarkIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { useEffect, useState } from 'react';
import IconButton from '@/components/IconButton';

interface TriggerRunModalProps {
  isOpen: boolean;
  workflowName: string;
  onClose: () => void;
  onTrigger: (initialInput: Record<string, any>) => void;
  triggering: boolean;
}

const methodTemplates: Record<string, object> = {
  send: {
    jsonrpc: '2.0',
    id: 1,
    method: 'message/send',
    params: {
      message: {
        role: 'user',
        parts: [{ kind: 'text', text: '' }],
        messageId: 'msg-00000000',
        contextId: 'ctx-00000000',
      },
      metadata: {},
    },
  },
  stream: {
    jsonrpc: '2.0',
    id: 2,
    method: 'message/stream',
    params: {
      message: {
        role: 'user',
        parts: [
          { kind: 'text', text: '' },
          { kind: 'file', file: { mimeType: 'application/pdf', uri: '' } },
        ],
        messageId: 'msg-00000000',
      },
      metadata: {},
    },
  },
  tasks: {
    jsonrpc: '2.0',
    id: 3,
    method: 'tasks/get',
    params: { id: 'task-00000000', historyLength: 10 },
  },
};

const TriggerRunModal: React.FC<TriggerRunModalProps> = ({ isOpen, workflowName, onClose, onTrigger, triggering }) => {
  const [jsonStr, setJsonStr] = useState('');
  const [isValid, setIsValid] = useState<boolean | null>(null);

  // Focus and layout reset
  useEffect(() => {
    if (isOpen) {
      setJsonStr('');
      setIsValid(null);
    }
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isOpen, onClose]);

  const validateJson = (val: string) => {
    if (!val.trim()) {
      setIsValid(null);
      return;
    }
    try {
      JSON.parse(val);
      setIsValid(true);
    } catch {
      setIsValid(false);
    }
  };

  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const val = e.target.value;
    setJsonStr(val);
    validateJson(val);
  };

  const formatJson = () => {
    if (isValid) {
      try {
        const parsed = JSON.parse(jsonStr);
        setJsonStr(JSON.stringify(parsed, null, 2));
      } catch {
        // ignore
      }
    }
  };

  const clearJson = () => {
    setJsonStr('');
    setIsValid(null);
  };

  const loadMethod = (key: string) => {
    const tpl = JSON.stringify(methodTemplates[key], null, 2);
    setJsonStr(tpl);
    setIsValid(true);
  };

  const handleRun = () => {
    if (isValid !== true) return;
    try {
      const parsed = JSON.parse(jsonStr);
      onTrigger(parsed);
    } catch {
      // ignore
    }
  };

  if (!isOpen) return null;

  return (
    <div
      className='fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4 py-6 backdrop-blur-sm'
      onClick={triggering ? undefined : onClose}
    >
      <div
        className='relative w-full max-w-[580px] rounded-xl border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)] shadow-2xl overflow-hidden'
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className='flex items-center justify-between px-5 py-4 border-b border-[color:var(--jarvis-border-soft)]'>
          <div className='flex items-center gap-2.5'>
            <div className='flex h-7 w-7 items-center justify-center rounded-md bg-[var(--jarvis-primary)] text-white shadow-sm'>
              <PlayIcon className='h-3.5 w-3.5' />
            </div>
            <div className='text-[15px] font-medium text-[var(--jarvis-text-strong)]'>
              Trigger run — {workflowName || 'Workflow'}
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

        {/* Body */}
        <div className='p-5'>
          {/* Label + Validation Tags */}
          <div className='mb-1.5 flex items-center gap-2 text-xs text-[var(--jarvis-subtle)]'>
            <span>Input JSON</span>
            {isValid === false && (
              <span className='rounded px-1.5 py-0.5 text-[10px] font-medium bg-[#3f1515] text-[#f87171]'>
                invalid JSON
              </span>
            )}
            {isValid === true && (
              <span className='rounded px-1.5 py-0.5 text-[10px] font-medium bg-[#0f291a] text-[#4ade80]'>valid</span>
            )}
          </div>

          {/* Textarea */}
          <textarea
            value={jsonStr}
            onChange={handleInputChange}
            spellCheck={false}
            className={`w-full min-h-[230px] rounded-lg border bg-[#0d1117] p-3 font-mono text-[13px] leading-relaxed text-[#e2e8f0] outline-none transition-colors resize-y shadow-inner ${
              isValid === false
                ? 'border-[#f87171] focus:border-[#f87171]'
                : isValid === true
                  ? 'border-[#4ade80] focus:border-[#4ade80]'
                  : 'border-[color:var(--jarvis-border)] focus:border-[var(--jarvis-primary)]'
            }`}
            placeholder='Paste or type your JSON here...'
          />

          {/* Toolbar */}
          <div className='mt-2 flex flex-wrap items-center gap-1.5'>
            <button
              type='button'
              onClick={formatJson}
              disabled={!isValid}
              className='flex items-center gap-1 rounded border border-[color:var(--jarvis-border)] bg-transparent px-2.5 py-1 text-[11px] text-[var(--jarvis-subtle)] transition-colors hover:border-[var(--jarvis-primary-soft)] hover:text-[var(--jarvis-text)] disabled:opacity-50 disabled:cursor-not-allowed'
            >
              <CodeBracketIcon className='h-3.5 w-3.5' /> Format
            </button>
            <button
              type='button'
              onClick={clearJson}
              disabled={jsonStr.length === 0}
              className='flex items-center gap-1 rounded border border-[color:var(--jarvis-border)] bg-transparent px-2.5 py-1 text-[11px] text-[var(--jarvis-subtle)] transition-colors hover:border-[var(--jarvis-primary-soft)] hover:text-[var(--jarvis-text)] disabled:opacity-50 disabled:cursor-not-allowed'
            >
              <TrashIcon className='h-3 w-3' /> Clear
            </button>
            <span className='mx-1 text-[13px] text-[var(--jarvis-border-strong)] select-none'>|</span>
            <button
              type='button'
              onClick={() => loadMethod('send')}
              className='rounded border border-[color:var(--jarvis-border)] bg-[var(--jarvis-bg)] px-2.5 py-1 font-mono text-[11px] text-[var(--jarvis-subtle)] transition-colors hover:border-[var(--jarvis-primary-soft)] hover:text-[var(--jarvis-primary-text)]'
            >
              message/send
            </button>
            <button
              type='button'
              onClick={() => loadMethod('stream')}
              className='rounded border border-[color:var(--jarvis-border)] bg-[var(--jarvis-bg)] px-2.5 py-1 font-mono text-[11px] text-[var(--jarvis-subtle)] transition-colors hover:border-[var(--jarvis-primary-soft)] hover:text-[var(--jarvis-primary-text)]'
            >
              message/stream
            </button>
            <button
              type='button'
              onClick={() => loadMethod('tasks')}
              className='rounded border border-[color:var(--jarvis-border)] bg-[var(--jarvis-bg)] px-2.5 py-1 font-mono text-[11px] text-[var(--jarvis-subtle)] transition-colors hover:border-[var(--jarvis-primary-soft)] hover:text-[var(--jarvis-primary-text)]'
            >
              tasks/get
            </button>
          </div>
        </div>

        {/* Footer */}
        <div className='flex items-center justify-end gap-2 border-t border-[color:var(--jarvis-border-soft)] bg-[var(--jarvis-card)] px-5 py-3.5'>
          <button
            type='button'
            onClick={onClose}
            disabled={triggering}
            className='rounded-md border border-[color:var(--jarvis-border)] bg-transparent px-4 py-1.5 text-[13px] text-[var(--jarvis-subtle)] transition-colors hover:border-[var(--jarvis-border-strong)] hover:text-[var(--jarvis-text)] disabled:cursor-not-allowed disabled:opacity-50'
          >
            Cancel
          </button>
          <button
            type='button'
            onClick={handleRun}
            disabled={isValid !== true || triggering}
            className='flex items-center gap-1.5 rounded-md border border-transparent bg-[var(--jarvis-primary)] px-4 py-1.5 text-[13px] font-medium text-white transition-colors hover:bg-[var(--jarvis-primary-hover)] disabled:cursor-not-allowed disabled:bg-[var(--jarvis-primary-muted)] disabled:text-[var(--jarvis-subtle)]'
          >
            {triggering ? (
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
            Run
          </button>
        </div>
      </div>
    </div>
  );
};

export default TriggerRunModal;
