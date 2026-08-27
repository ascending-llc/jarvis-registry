import { PencilSquareIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';

import SERVICES from '@/services';
import type { SkillFileContent, SkillFileMetadata } from '@/services/skill/type';

import type { EditorMode } from './types';

type SkillContentPanelProps = {
  skillId: string | null;
  selectedPath: string;
  markdown: string;
  markdownBody: string;
  markdownError: string | null;
  editorMode: EditorMode;
  canEdit: boolean;
  fileMetadata?: SkillFileMetadata;
  onMarkdownChange: (markdown: string) => void;
  onEditorModeChange: (mode: EditorMode) => void;
};

type FileLoadState =
  | { status: 'idle' | 'loading' }
  | { status: 'loaded'; data: SkillFileContent }
  | { status: 'error'; message: string };

const getErrorMessage = (error: unknown): string => {
  if (error && typeof error === 'object' && 'detail' in error) {
    const detail = (error as { detail?: string }).detail;
    if (detail) return detail;
  }
  return error instanceof Error ? error.message : 'Failed to load file.';
};

const formatBytes = (bytes?: number): string => {
  if (bytes === undefined) return 'Unknown size';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

const SkillContentPanel: React.FC<SkillContentPanelProps> = ({
  skillId,
  selectedPath,
  markdown,
  markdownBody,
  markdownError,
  editorMode,
  canEdit,
  fileMetadata,
  onMarkdownChange,
  onEditorModeChange,
}) => {
  const [fileState, setFileState] = useState<FileLoadState>({ status: 'idle' });
  const [retryToken, setRetryToken] = useState(0);
  const cacheRef = useRef<Map<string, SkillFileContent>>(new Map());
  const requestSequenceRef = useRef(0);
  const isSkillMarkdown = selectedPath === 'SKILL.md';

  useEffect(() => {
    cacheRef.current.clear();
    requestSequenceRef.current += 1;
    setFileState({ status: 'idle' });
  }, [skillId]);

  useEffect(() => {
    if (isSkillMarkdown) {
      requestSequenceRef.current += 1;
      setFileState({ status: 'idle' });
      return;
    }
    if (!skillId) {
      setFileState({ status: 'error', message: 'Save the skill before opening supporting files.' });
      return;
    }

    const cacheKey = `${skillId}:${selectedPath}`;
    const cached = cacheRef.current.get(cacheKey);
    if (cached) {
      setFileState({ status: 'loaded', data: cached });
      return;
    }

    const sequence = requestSequenceRef.current + 1;
    requestSequenceRef.current = sequence;
    setFileState({ status: 'loading' });

    SERVICES.SKILL.getSkillFile(skillId, selectedPath)
      .then(data => {
        if (requestSequenceRef.current !== sequence) return;
        cacheRef.current.set(cacheKey, data);
        setFileState({ status: 'loaded', data });
      })
      .catch(error => {
        if (requestSequenceRef.current !== sequence) return;
        setFileState({ status: 'error', message: getErrorMessage(error) });
      });

    return () => {
      if (requestSequenceRef.current === sequence) requestSequenceRef.current += 1;
    };
  }, [isSkillMarkdown, retryToken, selectedPath, skillId]);

  const renderSupportingFile = () => {
    if (fileState.status === 'idle' || fileState.status === 'loading') {
      return (
        <div className='flex min-h-72 flex-col items-center justify-center gap-3 text-sm text-[var(--jarvis-muted)]'>
          <div className='h-7 w-7 animate-spin rounded-full border-2 border-[color:var(--jarvis-border-strong)] border-b-[var(--jarvis-spinner)]' />
          Loading file...
        </div>
      );
    }

    if (fileState.status === 'error') {
      return (
        <div className='flex min-h-72 flex-col items-center justify-center px-6 text-center'>
          <p className='text-sm font-medium text-[var(--jarvis-danger-text)]'>{fileState.message}</p>
          <button type='button' onClick={() => setRetryToken(token => token + 1)} className='btn-secondary mt-4'>
            Retry
          </button>
        </div>
      );
    }

    const file = fileState.data;
    if (!file.available) {
      return (
        <div className='flex min-h-72 items-center justify-center px-6 text-center text-sm text-[var(--jarvis-muted)]'>
          {file.unavailableReason || 'This file is not available.'}
        </div>
      );
    }

    if (file.isBinary || fileMetadata?.isBinary) {
      const pathParts = selectedPath.split('/');
      return (
        <div className='flex min-h-72 flex-col items-center justify-center px-6 text-center'>
          <p className='font-medium text-[var(--jarvis-text-strong)]'>{pathParts[pathParts.length - 1]}</p>
          <p className='mt-2 text-sm text-[var(--jarvis-muted)]'>
            {file.mimeType} · {formatBytes(fileMetadata?.bytes)}
          </p>
          <p className='mt-4 text-sm text-[var(--jarvis-muted)]'>Binary files cannot be previewed as text.</p>
        </div>
      );
    }

    return (
      <pre className='min-h-72 max-w-full overflow-auto whitespace-pre p-6 font-mono text-[12.5px] leading-[1.7] text-[var(--jarvis-text)]'>
        <code>{file.content ?? ''}</code>
      </pre>
    );
  };

  return (
    <div className='flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden rounded-[14px] border border-[color:var(--jarvis-border)] bg-[var(--jarvis-card)]'>
      <div className='flex min-w-0 flex-shrink-0 items-center justify-between border-b border-[color:var(--jarvis-border)] px-4 py-3'>
        <span className='min-w-0 truncate font-mono text-xs text-[var(--jarvis-muted)]'>{selectedPath}</span>
        {isSkillMarkdown ? (
          canEdit && (
            <button
              type='button'
              aria-label={editorMode === 'edit' ? 'Preview SKILL.md' : 'Edit SKILL.md'}
              onClick={() => onEditorModeChange(editorMode === 'edit' ? 'preview' : 'edit')}
              className={`inline-flex h-7 w-7 items-center justify-center rounded-[7px] transition ${
                editorMode === 'edit'
                  ? 'bg-[var(--jarvis-primary-soft)] text-[var(--jarvis-primary-text)]'
                  : 'bg-[var(--jarvis-card-muted)] text-[var(--jarvis-muted)] hover:text-[var(--jarvis-text)]'
              }`}
            >
              <PencilSquareIcon className='h-[13px] w-[13px]' />
            </button>
          )
        ) : (
          <span className='px-2.5 py-1 text-[11px] text-[var(--jarvis-faint)]'>Read only</span>
        )}
      </div>

      <div className='min-h-0 min-w-0 flex-1 overflow-auto'>
        {isSkillMarkdown && editorMode === 'edit' && canEdit && (
          <textarea
            value={markdown}
            spellCheck={false}
            aria-label='SKILL.md content'
            onChange={event => onMarkdownChange(event.target.value)}
            className='h-full min-h-[360px] w-full resize-y border-0 bg-transparent px-7 py-6 font-mono text-[13px] leading-[1.7] text-[var(--jarvis-text)] outline-none focus:ring-0'
          />
        )}

        {isSkillMarkdown && (editorMode !== 'edit' || !canEdit) && (
          <div className='min-w-0 [overflow-wrap:anywhere] px-7 py-7 sm:px-9 sm:py-8'>
            {markdownError && (
              <div className='mb-5 rounded-lg border border-[color:var(--jarvis-danger)]/30 bg-[var(--jarvis-danger-soft)] px-4 py-3 text-sm text-[var(--jarvis-danger-text)]'>
                {markdownError}
              </div>
            )}
            {markdownError ? (
              <pre className='overflow-auto whitespace-pre-wrap font-mono text-[13px] leading-6 text-[var(--jarvis-text)]'>
                {markdown}
              </pre>
            ) : (
              <ReactMarkdown
                components={{
                  h1: ({ children }) => (
                    <h1 className='mb-4 text-2xl font-bold text-[var(--jarvis-text-strong)]'>{children}</h1>
                  ),
                  h2: ({ children }) => (
                    <h2 className='mb-2.5 mt-5 text-base font-bold text-[var(--jarvis-text-strong)]'>{children}</h2>
                  ),
                  h3: ({ children }) => (
                    <h3 className='mb-2 mt-4 text-[15px] font-bold text-[var(--jarvis-text-strong)]'>{children}</h3>
                  ),
                  p: ({ children }) => (
                    <p className='mb-4 min-w-0 break-words text-[14.5px] leading-[1.75] text-[var(--jarvis-text)]'>
                      {children}
                    </p>
                  ),
                  ul: ({ children }) => <ul className='my-0 min-w-0 list-disc space-y-2 pl-5'>{children}</ul>,
                  ol: ({ children }) => <ol className='my-0 min-w-0 list-decimal space-y-2 pl-5'>{children}</ol>,
                  li: ({ children }) => (
                    <li className='min-w-0 break-words text-sm leading-[1.6] text-[var(--jarvis-text)]'>{children}</li>
                  ),
                  strong: ({ children }) => (
                    <strong className='font-bold text-[var(--jarvis-text-strong)]'>{children}</strong>
                  ),
                  pre: ({ children }) => (
                    <pre className='my-4 max-w-full overflow-auto whitespace-pre rounded-lg bg-[var(--jarvis-card-muted)] p-4 text-[12.5px] leading-[1.7] [overflow-wrap:normal] [&>code]:whitespace-pre [&>code]:[overflow-wrap:normal]'>
                      {children}
                    </pre>
                  ),
                  code: ({ children }) => (
                    <code className='rounded bg-[var(--jarvis-card-muted)] px-1.5 py-0.5 font-mono text-[12.5px] [overflow-wrap:anywhere]'>
                      {children}
                    </code>
                  ),
                  a: ({ children, href }) => (
                    <a
                      href={href}
                      target='_blank'
                      rel='noreferrer'
                      className='break-words text-[var(--jarvis-primary-text)] underline'
                    >
                      {children}
                    </a>
                  ),
                  img: ({ src, alt, title }) => (
                    <img src={src} alt={alt ?? ''} title={title} className='h-auto max-w-full' />
                  ),
                }}
              >
                {markdownBody}
              </ReactMarkdown>
            )}
          </div>
        )}

        {!isSkillMarkdown && renderSupportingFile()}
      </div>
    </div>
  );
};

export default SkillContentPanel;
