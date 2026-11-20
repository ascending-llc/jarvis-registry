import { useEffect, useId, useMemo, useRef, useState } from 'react';

import SERVICES from '@/services';
import type { SkillScanResponse } from '@/services/skill/scanTypes';
import { SKILL_MARKDOWN_PATH } from './constants';
import SkillScanFindingCard from './SkillScanFindingCard';
import { composeSkillMarkdown } from './skillDraft';
import {
  formatScanDate,
  getFileScanFindings,
  getScanErrorMessage,
  getScanLineRange,
  groupScanFindingsByLocation,
  isScanForVersion,
  type LocatedScanFinding,
  SCAN_SEVERITY_COLORS,
  type SkillScanTarget,
} from './skillScan';

type Props = {
  skillId: string;
  skillVersion: number | null;
  path: string;
  sourceKey: string;
  sourceCache: Map<string, string>;
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

const SkillSourceView = ({
  skillId,
  skillVersion,
  path,
  sourceKey,
  sourceCache,
  scan,
  target,
  dirty,
  onShowFinding,
  onShowPreview,
  onRefreshContent,
  canRefreshContent,
}: Props) => {
  const [source, setSource] = useState<SourceState>(() => {
    const cached = sourceCache.get(sourceKey);
    return {
      key: sourceKey,
      content: cached ?? null,
      loading: cached === undefined,
      error: null,
      versionMatches: cached !== undefined,
    };
  });
  const [retry, setRetry] = useState(0);
  const [expandedLine, setExpandedLine] = useState<number | null>(null);
  const calloutId = useId();
  const lineRefs = useRef<Map<number, HTMLButtonElement>>(new Map());

  useEffect(() => {
    let disposed = false;
    setExpandedLine(null);
    const cached = sourceCache.get(sourceKey);
    if (cached !== undefined) {
      setSource({ key: sourceKey, content: cached, loading: false, error: null, versionMatches: true });
      return;
    }
    setSource({ key: sourceKey, content: null, loading: true, error: null, versionMatches: false });
    const acceptSource = (content: string, versionMatches: boolean) => {
      if (disposed) return;
      if (versionMatches) sourceCache.set(sourceKey, content);
      setSource({ key: sourceKey, content, loading: false, error: null, versionMatches });
    };
    const load = async () => {
      try {
        if (path === SKILL_MARKDOWN_PATH) {
          const detail = await SERVICES.SKILL.getSkillDetail(skillId);
          if (disposed) return;
          acceptSource(composeSkillMarkdown(detail), detail.version === skillVersion);
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
        acceptSource(content, detail.version === skillVersion);
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
  }, [path, skillId, skillVersion, sourceKey, sourceCache, retry]);

  const current = source.key === sourceKey;
  const lines = useMemo(() => (source.content ?? '').split(/\r\n|\n/), [source.content]);
  const findings = useMemo(() => getFileScanFindings(scan, path), [scan, path]);
  const versionMatches = current && source.versionMatches && isScanForVersion(scan, skillVersion);
  const canMark = versionMatches && !dirty;
  const { markers, findingLines } = useMemo(() => {
    const markers = new Map<number, LocatedScanFinding[]>();
    const findingLines = new Map<string, number>();
    if (!canMark) return { markers, findingLines };
    const composedMarkdown = path === SKILL_MARKDOWN_PATH ? lines.join('\n') : null;
    const located = findings.flatMap(finding => {
      const range = getScanLineRange(finding, lines.length, composedMarkdown);
      return range ? [{ finding, range }] : [];
    });
    for (const {
      findings: entries,
      range: [line],
    } of groupScanFindingsByLocation(located)) {
      markers.set(line, entries);
      for (const { finding } of entries) findingLines.set(finding.key, line);
    }
    return { markers, findingLines };
  }, [canMark, findings, lines, path]);
  const targetLine =
    target?.path === path && target.scanVersion === skillVersion ? findingLines.get(target.findingKey) : undefined;
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
    if (source.loading || !target || targetLine === undefined) return;
    setExpandedLine(targetLine);
    const button = lineRefs.current.get(targetLine);
    button?.scrollIntoView({ block: 'center' });
    button?.focus({ preventScroll: true });
  }, [source.loading, target, targetLine]);

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
  const unplaced = canMark ? findings.filter(finding => !findingLines.has(finding.key)).length : 0;
  if (dirty) notice = 'Unsaved changes. Line markers are hidden until the current content is saved and scanned.';
  else if (!source.versionMatches || (scan?.summary && !versionMatches))
    notice = 'Content changed since this report. Findings remain available in Security.';
  else if (target?.path === path && targetLine === undefined)
    notice =
      target.startLine === null
        ? 'The report lists this file without a line number.'
        : `The scanner reported line ${target.startLine}. Its location could not be verified in this file.`;
  else if (unplaced > 0)
    notice = `${unplaced} ${unplaced === 1 ? 'finding could' : 'findings could'} not be placed on a source line. See Security for details.`;

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
      <div className='my-2 bg-[var(--jarvis-card-muted)] py-2 font-mono text-[12.5px] leading-6 text-[var(--jarvis-text)]'>
        {lines.map((line, index) => {
          const lineNumber = index + 1;
          const entries = markers.get(lineNumber) ?? [];
          const highest = entries[0]?.finding.severity;
          const expanded = expandedLine === lineNumber;
          return (
            <div key={lineNumber}>
              <div
                className='flex min-w-0 items-start'
                style={
                  expanded && highest
                    ? {
                        backgroundColor: `color-mix(in srgb, ${SCAN_SEVERITY_COLORS[highest]} 10%, transparent)`,
                      }
                    : undefined
                }
              >
                <span aria-hidden='true' className='flex h-6 w-3 shrink-0 justify-center'>
                  {highest && (
                    <span className='w-[3px] rounded-full' style={{ backgroundColor: SCAN_SEVERITY_COLORS[highest] }} />
                  )}
                </span>
                <span className='flex h-6 w-6 shrink-0 items-center justify-center'>
                  {highest && (
                    <button
                      type='button'
                      ref={node => {
                        if (node) lineRefs.current.set(lineNumber, node);
                        else lineRefs.current.delete(lineNumber);
                      }}
                      aria-label={`Line ${lineNumber}, ${highest} severity, ${entries.length} ${entries.length === 1 ? 'finding' : 'findings'}`}
                      title={
                        entries.length === 1 ? entries[0].finding.title : `${entries.length} findings at this location`
                      }
                      aria-expanded={expanded}
                      aria-controls={expanded ? calloutId : undefined}
                      className='group grid h-6 w-6 place-items-center rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-inset focus-visible:outline-[var(--jarvis-primary)]'
                      onClick={() => setExpandedLine(value => (value === lineNumber ? null : lineNumber))}
                      style={{ color: SCAN_SEVERITY_COLORS[highest] }}
                    >
                      <span
                        aria-hidden='true'
                        className={`h-2.5 w-2.5 rounded-full border-2 border-[color:var(--jarvis-card-muted)] bg-current ring-1 ring-current transition-transform group-hover:scale-125 ${expanded ? 'scale-125' : ''}`}
                      />
                    </button>
                  )}
                </span>
                <span
                  aria-hidden='true'
                  className='w-10 flex-none select-none pr-3 text-right tabular-nums text-[var(--jarvis-muted)]'
                >
                  {lineNumber}
                </span>
                <code className='min-w-0 flex-1 whitespace-pre-wrap break-words pr-4'>{line || '\u00a0'}</code>
              </div>
              {expanded && entries.length > 0 && (
                <div id={calloutId} className='mb-2 ml-9 mr-3 mt-1.5 max-w-[560px] space-y-2 font-sans sm:ml-[76px]'>
                  {entries.map(({ finding }) => (
                    <SkillScanFindingCard key={finding.key} finding={finding} onShowFinding={onShowFinding} />
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
