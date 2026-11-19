import { useEffect, useMemo, useRef, useState } from 'react';

import SERVICES from '@/services';
import type { SkillScanResponse, SkillScanSeverity } from '@/services/skill/scanTypes';
import { SKILL_MARKDOWN_PATH } from './constants';
import { composeSkillMarkdown } from './skillDraft';
import {
  formatScanDate,
  getScanErrorMessage,
  type IndexedScanFinding,
  indexScanFindings,
  resolveScanFilePath,
  SCAN_SEVERITIES,
  SCAN_SEVERITY_CLASSES,
  type SkillScanTarget,
} from './skillScan';

type Props = {
  skillId: string;
  skillVersion: number | null;
  path: string;
  scan: SkillScanResponse | null;
  target: SkillScanTarget | null;
  dirty: boolean;
  onShowFinding: (key: string) => void;
  onShowPreview: () => void;
  onRefreshContent?: () => void;
  canRefreshContent: boolean;
};

type SourceState = {
  key: string;
  content: string | null;
  loading: boolean;
  error: string | null;
  versionMatches: boolean;
};

const LINE_MARKER_COLORS: Record<SkillScanSeverity, string> = {
  critical: 'text-[var(--jarvis-danger-text)]',
  high: 'text-[var(--jarvis-orange)]',
  medium: 'text-[var(--jarvis-warning-text)]',
  low: 'text-[var(--jarvis-muted)]',
  info: 'text-[var(--jarvis-info-text)]',
};

const getRange = (
  finding: IndexedScanFinding,
  lineCount: number,
  composedMarkdown: string | null,
): [number, number] | null => {
  const start = finding.startLine;
  const end = finding.endLine ?? start;
  if (start === null || end === null || !Number.isInteger(start) || !Number.isInteger(end)) return null;
  if (start < 1 || end < start) return null;
  // 主文件的 frontmatter 由详情数据重组，原始行号不一定适用于展示内容。
  if (composedMarkdown !== null) {
    const snippet = finding.snippet?.replace(/\r\n/g, '\n').trim();
    if (!snippet) return null;
    const offset = composedMarkdown.indexOf(snippet);
    if (offset < 0 || composedMarkdown.indexOf(snippet, offset + 1) !== -1) return null;
    const matchedStart = composedMarkdown.slice(0, offset).split('\n').length;
    const matchedEnd = matchedStart + snippet.split('\n').length - 1;
    return [matchedStart, matchedEnd];
  }
  if (end > lineCount) return null;
  return [start, end];
};

const SkillSourceView = ({
  skillId,
  skillVersion,
  path,
  scan,
  target,
  dirty,
  onShowFinding,
  onShowPreview,
  onRefreshContent,
  canRefreshContent,
}: Props) => {
  const sourceKey = `${skillId}:${skillVersion}:${path}`;
  const [source, setSource] = useState<SourceState>({
    key: '',
    content: null,
    loading: true,
    error: null,
    versionMatches: false,
  });
  const [retry, setRetry] = useState(0);
  const [expandedLine, setExpandedLine] = useState<number | null>(null);
  const lineRefs = useRef<Map<number, HTMLButtonElement>>(new Map());
  const reportKey = scan?.summary?.scannedAt;

  useEffect(() => {
    let disposed = false;
    setExpandedLine(null);
    setSource({ key: sourceKey, content: null, loading: true, error: null, versionMatches: false });
    const load = async () => {
      try {
        if (path === SKILL_MARKDOWN_PATH) {
          const detail = await SERVICES.SKILL.getSkillDetail(skillId);
          if (disposed) return;
          setSource({
            key: sourceKey,
            content: composeSkillMarkdown(detail),
            loading: false,
            error: null,
            versionMatches: detail.version === skillVersion,
          });
          return;
        }
        const file = await SERVICES.SKILL.getSkillFile(skillId, path);
        if (disposed) return;
        if (!file.available) throw new Error(file.unavailableReason || 'This file is unavailable.');
        if (file.isBinary) throw new Error('Binary files cannot be previewed as text.');
        if (typeof file.content !== 'string')
          throw new Error('Original file text is unavailable. Open Preview to read the skill.');
        const content = file.content;
        const detail = await SERVICES.SKILL.getSkillDetail(skillId);
        if (disposed) return;
        setSource({
          key: sourceKey,
          content,
          loading: false,
          error: null,
          versionMatches: detail.version === skillVersion,
        });
      } catch (error) {
        if (!disposed)
          setSource({
            key: sourceKey,
            content: null,
            loading: false,
            error: getScanErrorMessage(error, 'Failed to load original file.'),
            versionMatches: false,
          });
      }
    };
    void load();
    return () => {
      disposed = true;
    };
  }, [path, skillId, skillVersion, sourceKey, retry, reportKey]);

  const current = source.key === sourceKey;
  const lines = useMemo(() => (source.content ?? '').split(/\r\n|\n/), [source.content]);
  const findings = useMemo(
    () => indexScanFindings(scan).filter(finding => resolveScanFilePath(finding.filePath, [path]) === path),
    [scan, path],
  );
  const versionMatches = Boolean(
    current &&
      source.versionMatches &&
      scan?.summary &&
      !scan.stale &&
      scan.summary.skillVersion === skillVersion &&
      scan.skillVersion === skillVersion,
  );
  const canMark = versionMatches && !dirty;
  const { markers, ranges } = useMemo(() => {
    const markers = new Map<number, IndexedScanFinding[]>();
    const ranges = new Map<string, [number, number]>();
    if (!canMark) return { markers, ranges };
    const composedMarkdown = path === SKILL_MARKDOWN_PATH ? lines.join('\n') : null;
    for (const finding of findings) {
      const range = getRange(finding, lines.length, composedMarkdown);
      if (!range) continue;
      ranges.set(finding.key, range);
      const entries = markers.get(range[0]);
      if (entries) entries.push(finding);
      else markers.set(range[0], [finding]);
    }
    return { markers, ranges };
  }, [canMark, findings, lines, path]);
  const targetRange = target && target.scanVersion === skillVersion ? ranges.get(target.findingKey) : undefined;
  const targetLine = targetRange?.[0] ?? null;
  const activeFindings = expandedLine === null ? [] : (markers.get(expandedLine) ?? []);
  const highlightEnd =
    expandedLine === targetLine && targetRange
      ? targetRange[1]
      : activeFindings.reduce((end, finding) => Math.max(end, ranges.get(finding.key)?.[1] ?? 0), 0);
  const backToFinding = target?.path === path && (
    <button
      type='button'
      onClick={() => onShowFinding(target.findingKey)}
      className='text-[var(--jarvis-primary-text)] underline underline-offset-2'
    >
      Back to finding
    </button>
  );

  useEffect(() => {
    if (source.loading || target?.path !== path || targetLine === null) return;
    setExpandedLine(targetLine);
    const button = lineRefs.current.get(targetLine);
    button?.scrollIntoView({ block: 'center' });
    button?.focus({ preventScroll: true });
  }, [source.loading, target, targetLine, path]);

  if (!current || source.loading)
    return (
      <p role='status' className='p-6 text-sm text-[var(--jarvis-muted)]'>
        Loading file contents…
      </p>
    );
  if (source.error)
    return (
      <div role='alert' className='p-6 text-sm text-[var(--jarvis-muted)]'>
        <p>{source.error}</p>
        <div className='mt-3 flex flex-wrap items-center gap-3'>
          <button type='button' className='btn-secondary' onClick={() => setRetry(value => value + 1)}>
            Retry
          </button>
          <button type='button' className='btn-secondary' onClick={onShowPreview}>
            Open Preview
          </button>
          {backToFinding}
        </div>
      </div>
    );

  let notice: string | null = null;
  if (dirty) notice = 'Unsaved changes. Line markers are hidden until the current content is saved and scanned.';
  else if (!source.versionMatches || (scan?.summary && !versionMatches))
    notice = 'Content changed since this report. Findings remain available in Security.';
  else if (target?.path === path && !targetRange)
    notice =
      target.startLine === null
        ? 'The report lists this file without a line number.'
        : `The scanner reported line ${target.startLine}. Its location could not be verified in this file.`;
  else if (
    path === SKILL_MARKDOWN_PATH &&
    findings.some(finding => finding.startLine !== null && !ranges.has(finding.key))
  )
    notice = 'Some scan locations could not be matched to the displayed file. See Security for the reported locations.';

  return (
    <div>
      {notice && (
        <div className='flex flex-wrap items-center gap-2 bg-[var(--jarvis-warning-soft)] px-4 py-3 text-xs text-[var(--jarvis-warning-text)]'>
          <p role='status'>{notice}</p>
          {backToFinding}
        </div>
      )}
      {!source.versionMatches && onRefreshContent && (
        <div className='px-4 pb-3'>
          <button type='button' className='btn-secondary' disabled={!canRefreshContent} onClick={onRefreshContent}>
            Reload saved content
          </button>
          {!canRefreshContent && (
            <p className='mt-2 text-xs text-[var(--jarvis-muted)]'>
              Save or reset your edits before reloading saved content.
            </p>
          )}
        </div>
      )}
      {markers.size > 0 && scan?.summary && (
        <p className='px-4 pt-3 text-xs text-[var(--jarvis-muted)]'>
          Markers from the scan of version {scan.summary.skillVersion} · {formatScanDate(scan.summary.scannedAt)}
        </p>
      )}
      <div className='max-w-full overflow-x-auto py-3 font-mono text-xs leading-6 text-[var(--jarvis-text)]'>
        {lines.map((line, index) => {
          const lineNumber = index + 1;
          const entries = markers.get(lineNumber) ?? [];
          const highest = SCAN_SEVERITIES.find(severity => entries.some(finding => finding.severity === severity));
          const highlighted =
            entries.length > 0 || (expandedLine !== null && lineNumber >= expandedLine && lineNumber <= highlightEnd);
          return (
            <div key={lineNumber}>
              <div className={`flex min-w-full items-start ${highlighted ? 'bg-[var(--jarvis-warning-soft)]' : ''}`}>
                <span
                  aria-hidden='true'
                  className='w-12 flex-none select-none pr-2 text-right text-[var(--jarvis-muted)]'
                >
                  {lineNumber}
                </span>
                <span className='w-7 flex-none'>
                  {entries.length > 0 && (
                    <button
                      type='button'
                      ref={node => {
                        if (node) lineRefs.current.set(lineNumber, node);
                        else lineRefs.current.delete(lineNumber);
                      }}
                      aria-label={`Line ${lineNumber}, ${entries.length} scan findings`}
                      aria-expanded={expandedLine === lineNumber}
                      className={`w-6 font-bold ${LINE_MARKER_COLORS[highest ?? 'low']}`}
                      onClick={() => setExpandedLine(value => (value === lineNumber ? null : lineNumber))}
                    >
                      ●
                    </button>
                  )}
                </span>
                <code className='min-w-0 flex-1 whitespace-pre-wrap break-words pr-4'>{line || '\u00a0'}</code>
              </div>
              {expandedLine === lineNumber && entries.length > 0 && (
                <div className='space-y-3 border-y border-[var(--jarvis-border)] bg-[var(--jarvis-card)] p-4 font-sans'>
                  {entries.map(finding => (
                    <div key={finding.key}>
                      <span className={`mr-2 rounded px-2 py-1 ${SCAN_SEVERITY_CLASSES[finding.severity]}`}>
                        {finding.severity}
                      </span>
                      <strong>{finding.title}</strong>
                      <p className='mt-1 text-[var(--jarvis-muted)]'>{finding.tool}</p>
                      <p>{finding.message}</p>
                      <button
                        type='button'
                        className='mt-1 text-[var(--jarvis-primary-text)] underline'
                        onClick={() => onShowFinding(finding.key)}
                      >
                        View in Security
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default SkillSourceView;
