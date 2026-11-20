import { PencilSquareIcon } from '@heroicons/react/24/outline';
import type React from 'react';
import { useEffect, useMemo, useRef, useState } from 'react';

import SERVICES from '@/services';
import type { SkillScanResponse } from '@/services/skill/scanTypes';
import type { SkillFileContent, SkillFileMetadata } from '@/services/skill/type';
import { SKILL_MARKDOWN_PATH } from './constants';
import HighlightedTextarea from './HighlightedTextarea';
import SkillMarkdownPreview from './SkillMarkdownPreview';
import { SkillPreviewScanText } from './SkillPreviewScanMarker';
import SkillSourceView from './SkillSourceView';
import { createMarkdownScanPlugin } from './skillPreviewScanMarkdown';
import {
  getFileScanFindings,
  getScanLineRange,
  groupScanFindingsByLocation,
  isScanForVersion,
  type SkillScanTarget,
} from './skillScan';
import { getLanguageFromPath, highlightCode, highlightSkillMarkdown } from './syntaxHighlighting';
import type { EditorMode } from './types';

type SkillContentPanelProps = {
  skillId: string | null;
  skillVersion?: number | null;
  scan?: SkillScanResponse | null;
  scanTarget?: SkillScanTarget | null;
  hasUnsavedChanges?: boolean;
  onShowFinding?: (key: string) => void;
  onRefreshContent?: () => void;
  selectedPath: string;
  markdown: string;
  markdownBody: string;
  frontmatterSource: string;
  markdownError: string | null;
  editorMode: EditorMode;
  canEdit: boolean;
  fileMetadata?: SkillFileMetadata;
  availableFilePaths?: readonly string[];
  onSelectFile?: (path: string) => void;
  onMarkdownChange: (markdown: string) => void;
  onEditorModeChange: (mode: EditorMode) => void;
};

type FileLoadState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'loaded'; key: string; data: SkillFileContent }
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
  skillVersion = null,
  scan = null,
  scanTarget = null,
  hasUnsavedChanges = false,
  onShowFinding,
  onRefreshContent,
  selectedPath,
  markdown,
  markdownBody,
  frontmatterSource,
  markdownError,
  editorMode,
  canEdit,
  fileMetadata,
  availableFilePaths = [],
  onSelectFile,
  onMarkdownChange,
  onEditorModeChange,
}) => {
  const [storedFileState, setFileState] = useState<FileLoadState>({ status: 'idle' });
  const [retryToken, setRetryToken] = useState(0);
  const [sourceMode, setSourceMode] = useState(false);
  const [sourceCache] = useState(() => new Map<string, string>());
  const showSource = sourceMode && editorMode !== 'edit' && Boolean(skillId);

  useEffect(() => {
    if (scanTarget?.path === selectedPath) setSourceMode(true);
  }, [scanTarget, selectedPath]);
  const cacheRef = useRef<Map<string, SkillFileContent>>(new Map());
  const requestSequenceRef = useRef(0);
  const isSkillMarkdown = selectedPath === SKILL_MARKDOWN_PATH;
  const reportKey = `${scan?.summary?.contentDigest ?? ''}:${scan?.summary?.scannedAt ?? ''}`;
  const fileKey = `${skillId}:${skillVersion}:${selectedPath}`;

  useEffect(() => {
    sourceCache.clear();
  }, [skillId, skillVersion, reportKey, sourceCache]);

  const fileState = storedFileState.status === 'loaded' && storedFileState.key !== fileKey ? null : storedFileState;
  const frontmatterCode = `---\n${frontmatterSource}\n---`;
  const bodyLineOffset = frontmatterCode.split(/\r\n|\n/).length;
  const highlightedFrontmatter = useMemo(() => highlightCode(frontmatterCode, 'yaml'), [frontmatterCode]);
  const highlightedInvalidMarkdown = useMemo(() => highlightSkillMarkdown(markdown), [markdown]);
  const supportingFile = fileState?.status === 'loaded' ? fileState.data : null;
  const previewFindings = useMemo(() => {
    if (showSource || scan?.skillId !== skillId || !isScanForVersion(scan, skillVersion)) return [];
    const findings = getFileScanFindings(scan, selectedPath);
    if (!findings.length) return [];
    const content = isSkillMarkdown
      ? !hasUnsavedChanges && !markdownError && editorMode !== 'edit'
        ? markdown
        : null
      : supportingFile?.available && !supportingFile.isBinary && !fileMetadata?.isBinary
        ? supportingFile.content
        : null;
    if (typeof content !== 'string') return [];
    const normalized = content.replace(/\r\n/g, '\n');
    const lineCount = normalized.split('\n').length;
    return findings.flatMap(finding => {
      const range = getScanLineRange(finding, lineCount, isSkillMarkdown ? normalized : null);
      return range ? [{ finding, range }] : [];
    });
  }, [
    showSource,
    scan,
    skillId,
    skillVersion,
    selectedPath,
    isSkillMarkdown,
    hasUnsavedChanges,
    markdownError,
    editorMode,
    markdown,
    supportingFile,
    fileMetadata?.isBinary,
  ]);
  const markdownScan = useMemo(() => {
    const groups = isSkillMarkdown ? groupScanFindingsByLocation(previewFindings) : [];
    const frontmatter = groups.filter(({ range }) => range[0] <= bodyLineOffset).flatMap(group => group.findings);
    const body = groups.filter(({ range }) => range[0] > bodyLineOffset).flatMap(group => group.findings);
    return {
      frontmatter,
      body: new Map(body.map(entry => [entry.finding.key, entry])),
      plugins: body.length > 0 ? [createMarkdownScanPlugin(body, bodyLineOffset)] : [],
    };
  }, [bodyLineOffset, isSkillMarkdown, previewFindings]);
  const highlightedSupportingFile = useMemo(() => {
    if (!supportingFile?.available || supportingFile.isBinary) return null;
    if (fileMetadata?.isBinary) return null;

    const language = getLanguageFromPath(selectedPath);
    if (!language) return null;

    return {
      html: highlightCode(supportingFile.content ?? '', language),
      language,
    };
  }, [fileMetadata?.isBinary, supportingFile, selectedPath]);

  useEffect(() => {
    cacheRef.current.clear();
    requestSequenceRef.current += 1;
    setFileState({ status: 'idle' });
  }, [skillId, skillVersion]);

  useEffect(() => {
    if (isSkillMarkdown || showSource) {
      requestSequenceRef.current += 1;
      setFileState({ status: 'idle' });
      return;
    }
    if (!skillId) {
      setFileState({ status: 'error', message: 'Save the skill before opening supporting files.' });
      return;
    }

    const sequence = ++requestSequenceRef.current;
    const cached = cacheRef.current.get(fileKey);
    if (cached) {
      setFileState({ status: 'loaded', key: fileKey, data: cached });
      return;
    }

    setFileState({ status: 'loading' });

    SERVICES.SKILL.getSkillFile(skillId, selectedPath)
      .then(data => {
        if (requestSequenceRef.current !== sequence) return;
        cacheRef.current.set(fileKey, data);
        setFileState({ status: 'loaded', key: fileKey, data });
      })
      .catch(error => {
        if (requestSequenceRef.current !== sequence) return;
        setFileState({ status: 'error', message: getErrorMessage(error) });
      });

    return () => {
      if (requestSequenceRef.current === sequence) requestSequenceRef.current += 1;
    };
  }, [isSkillMarkdown, retryToken, selectedPath, skillId, fileKey, showSource]);

  const renderSupportingFile = () => {
    if (!fileState || fileState.status === 'idle' || fileState.status === 'loading') {
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

    if (previewFindings.length > 0 && typeof file.content === 'string') {
      return (
        <div className='relative min-h-72 px-7 py-6 sm:px-9'>
          <SkillPreviewScanText
            key={`${fileKey}:${reportKey}`}
            text={file.content}
            firstLine={1}
            language={highlightedSupportingFile?.language}
            findings={previewFindings}
            onShowFinding={onShowFinding}
          />
        </div>
      );
    }

    if (highlightedSupportingFile) {
      return (
        <pre className='min-h-72 max-w-full overflow-auto whitespace-pre p-6 font-mono text-[12.5px] leading-[1.7] text-[var(--jarvis-text)]'>
          <code
            className={`skill-syntax-highlight language-${highlightedSupportingFile.language}`}
            dangerouslySetInnerHTML={{ __html: highlightedSupportingFile.html }}
          />
        </pre>
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
        {skillId && (
          <div className='ml-auto mr-3 flex gap-2 text-xs'>
            <button
              type='button'
              aria-pressed={!showSource}
              onClick={() => setSourceMode(false)}
              className={!showSource ? 'text-[var(--jarvis-primary-text)]' : 'text-[var(--jarvis-muted)]'}
            >
              Preview
            </button>
            <button
              type='button'
              aria-pressed={showSource}
              onClick={() => {
                setSourceMode(true);
                onEditorModeChange('preview');
              }}
              className={showSource ? 'text-[var(--jarvis-primary-text)]' : 'text-[var(--jarvis-muted)]'}
            >
              Source
            </button>
          </div>
        )}
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
        {showSource && skillId && (
          <SkillSourceView
            key={fileKey}
            skillId={skillId}
            skillVersion={skillVersion}
            path={selectedPath}
            sourceKey={`${fileKey}:${reportKey}`}
            sourceCache={sourceCache}
            scan={scan}
            target={scanTarget}
            dirty={hasUnsavedChanges && isSkillMarkdown}
            onShowFinding={key => onShowFinding?.(key)}
            onShowPreview={() => setSourceMode(false)}
            onRefreshContent={onRefreshContent}
            canRefreshContent={!hasUnsavedChanges}
          />
        )}
        {!showSource && (
          <>
            {isSkillMarkdown && editorMode === 'edit' && canEdit && (
              <HighlightedTextarea value={markdown} ariaLabel='SKILL.md content' onChange={onMarkdownChange} />
            )}

            {isSkillMarkdown && (editorMode !== 'edit' || !canEdit) && (
              <div
                key={`${fileKey}:${reportKey}`}
                className='relative min-w-0 [overflow-wrap:anywhere] px-7 py-7 sm:px-9 sm:py-8'
              >
                {markdownError && (
                  <div className='mb-5 rounded-lg border border-[color:var(--jarvis-danger)]/30 bg-[var(--jarvis-danger-soft)] px-4 py-3 text-sm text-[var(--jarvis-danger-text)]'>
                    {markdownError}
                  </div>
                )}
                {markdownError ? (
                  <pre
                    className='skill-syntax-highlight overflow-auto whitespace-pre-wrap font-mono text-[13px] leading-6 text-[var(--jarvis-text)]'
                    dangerouslySetInnerHTML={{ __html: highlightedInvalidMarkdown }}
                  />
                ) : (
                  <>
                    {markdownScan.frontmatter.length > 0 ? (
                      <div className='mb-6'>
                        <SkillPreviewScanText
                          text={frontmatterCode}
                          firstLine={1}
                          language='yaml'
                          findings={markdownScan.frontmatter}
                          onShowFinding={onShowFinding}
                          wrap
                          codeBlock
                        />
                      </div>
                    ) : (
                      <pre
                        className='skill-syntax-highlight mb-6 max-w-full overflow-auto whitespace-pre-wrap rounded-lg bg-[var(--jarvis-card-muted)] p-4 font-mono text-[12.5px] leading-[1.7] text-[var(--jarvis-text)]'
                        dangerouslySetInnerHTML={{ __html: highlightedFrontmatter }}
                      />
                    )}
                    <SkillMarkdownPreview
                      body={markdownBody}
                      bodyLineOffset={bodyLineOffset}
                      plugins={markdownScan.plugins}
                      findings={markdownScan.body}
                      availableFilePaths={availableFilePaths}
                      onSelectFile={onSelectFile}
                      onShowFinding={onShowFinding}
                    />
                  </>
                )}
              </div>
            )}

            {!isSkillMarkdown && renderSupportingFile()}
          </>
        )}
      </div>
    </div>
  );
};

export default SkillContentPanel;
