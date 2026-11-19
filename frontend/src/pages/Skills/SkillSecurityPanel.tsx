import { ChevronDownIcon } from '@heroicons/react/24/outline';
import { useEffect, useId, useRef, useState } from 'react';
import type { SkillScanResponse, SkillScanSeverity } from '@/services/skill/scanTypes';
import {
  formatScanDate,
  type IndexedScanFinding,
  indexScanFindings,
  resolveScanFilePath,
  SCAN_REASON_LABELS,
  SCAN_SEVERITIES,
  SCAN_SEVERITY_CLASSES,
  type SkillScanTarget,
} from './skillScan';

type SkillSecurityPanelProps = {
  scan: SkillScanResponse | null;
  loading: boolean;
  error: string | null;
  availableFilePaths: readonly string[];
  selectedFindingKey: string | null;
  canRequestScan: boolean;
  onLocate: (target: SkillScanTarget) => void;
};

const SkillSecurityPanel = ({
  scan,
  loading,
  error,
  availableFilePaths,
  selectedFindingKey,
  canRequestScan,
  onLocate,
}: SkillSecurityPanelProps) => {
  const findingRefs = useRef(new Map<string, HTMLDivElement>());
  const focusPending = useRef<string | null>(null);
  const id = useId();
  const [severity, setSeverity] = useState('all');
  const [source, setSource] = useState('all');
  const [showInfo, setShowInfo] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [detailsOpen, setDetailsOpen] = useState(false);
  useEffect(() => {
    setSeverity('all');
    setSource('all');
    setShowInfo(false);
    setExpanded(null);
    setDetailsOpen(false);
  }, [scan?.skillId]);
  useEffect(() => setExpanded(null), [scan?.summary?.contentDigest, scan?.summary?.scannedAt]);
  useEffect(() => {
    focusPending.current = selectedFindingKey;
    if (selectedFindingKey) {
      setSeverity('all');
      setSource('all');
      setShowInfo(true);
      setExpanded(selectedFindingKey);
    }
  }, [selectedFindingKey]);

  useEffect(() => {
    if (!selectedFindingKey || focusPending.current !== selectedFindingKey || expanded !== selectedFindingKey) return;
    const element = findingRefs.current.get(selectedFindingKey);
    if (!element) return;
    focusPending.current = null;
    element.focus({ preventScroll: true });
    element.scrollIntoView({ block: 'nearest' });
  }, [selectedFindingKey, severity, source, showInfo, expanded]);

  const indexedFindings = indexScanFindings(scan);
  const renderFinding = (finding: IndexedScanFinding) => {
    const resolvedPath = resolveScanFilePath(finding.filePath, availableFilePaths);
    const scanVersion = scan?.summary?.skillVersion;
    const canLocate = resolvedPath !== null && scanVersion !== undefined;
    const lineRange =
      finding.startLine !== null
        ? ` · L${finding.startLine}${finding.endLine !== null && finding.endLine !== finding.startLine ? `–${finding.endLine}` : ''}`
        : '';
    const location = finding.filePath ? `${finding.filePath}${lineRange}` : null;

    const borderColors: Record<SkillScanSeverity, string> = {
      critical: 'border-l-[var(--jarvis-danger)]',
      high: 'border-l-[var(--jarvis-orange)]',
      medium: 'border-l-[var(--jarvis-warning)]',
      low: 'border-l-[var(--jarvis-muted)]',
      info: 'border-l-[var(--jarvis-info-text)]',
    };
    const isExpanded = expanded === finding.key;
    return (
      <div
        key={finding.key}
        ref={element => {
          if (element) findingRefs.current.set(finding.key, element);
          else findingRefs.current.delete(finding.key);
        }}
        tabIndex={-1}
        className={`overflow-hidden rounded-xl border border-[var(--jarvis-border)] border-l-[3px] bg-[var(--jarvis-card)] outline-none focus:ring-2 focus:ring-[var(--jarvis-primary)] ${borderColors[finding.severity]}`}
      >
        <div className='flex flex-wrap items-center gap-x-3 gap-y-2 px-4 pt-3.5'>
          <span
            className={`rounded-md border border-current px-2 py-0.5 text-xs font-semibold capitalize ${SCAN_SEVERITY_CLASSES[finding.severity]}`}
          >
            {finding.severity}
          </span>
          <span className='rounded-md border border-[var(--jarvis-border-strong)] px-2 py-0.5 text-xs capitalize text-[var(--jarvis-muted)]'>
            {finding.category.replaceAll('_', ' ')}
          </span>
          <button
            type='button'
            aria-expanded={isExpanded}
            aria-controls={`${id}-finding-${finding.key}`}
            onClick={() => setExpanded(isExpanded ? null : finding.key)}
            className='inline-flex min-w-[min(12rem,100%)] flex-1 items-center gap-1.5 break-words text-left text-sm font-semibold text-[var(--jarvis-text-strong)] hover:text-[var(--jarvis-primary-text)]'
          >
            <ChevronDownIcon aria-hidden='true' className={`h-3 w-3 shrink-0 ${isExpanded ? 'rotate-180' : ''}`} />
            <span className='min-w-0'>{finding.title}</span>
          </button>
          {location &&
            (canLocate ? (
              <button
                type='button'
                onClick={() => {
                  if (resolvedPath === null || scanVersion === undefined) return;
                  onLocate({
                    findingKey: finding.key,
                    path: resolvedPath,
                    startLine: finding.startLine,
                    endLine: finding.endLine,
                    scanVersion,
                  });
                }}
                className='break-all text-left font-mono text-xs text-[var(--jarvis-primary-text)] hover:underline'
                aria-label={`Locate ${finding.title} in ${location}`}
              >
                {location} ↗
              </button>
            ) : (
              <span
                title='This file or scanned version is unavailable'
                className='break-all font-mono text-xs text-[var(--jarvis-muted)]'
              >
                {location}
              </span>
            ))}
        </div>
        <div className='flex flex-wrap items-center gap-2 px-4 py-3 text-xs text-[var(--jarvis-muted)]'>
          <span className='rounded bg-[var(--jarvis-card-muted)] px-2 py-0.5'>{finding.tool}</span>
          {finding.toolCategory && <span>{finding.toolCategory}</span>}
          {finding.ruleId && <span>{finding.ruleId}</span>}
        </div>
        <div
          id={`${id}-finding-${finding.key}`}
          hidden={!isExpanded}
          className='space-y-3 border-t border-[var(--jarvis-border)] px-4 py-3 text-xs leading-relaxed text-[var(--jarvis-text)]'
        >
          {isExpanded && (
            <>
              {finding.snippet && (
                <pre className='max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-lg border border-[var(--jarvis-border)] bg-[var(--jarvis-card-muted)] p-3'>
                  <code>{finding.snippet}</code>
                </pre>
              )}
              <p className='whitespace-pre-wrap break-words'>
                <strong className='text-[var(--jarvis-text-strong)]'>Why it matters. </strong>
                {finding.message}
              </p>
              {finding.remediation && (
                <p className='whitespace-pre-wrap break-words'>
                  <strong className='text-[var(--jarvis-text-strong)]'>Suggested fix. </strong>
                  {finding.remediation}
                </p>
              )}
              {finding.confidence !== null && (
                <p className='text-[var(--jarvis-muted)]'>Confidence: {finding.confidence}</p>
              )}
              {location && !canLocate && (
                <p className='text-[var(--jarvis-muted)]'>
                  {resolvedPath === null
                    ? 'This file is not available in the current skill content.'
                    : 'The scanned version is unavailable, so this finding cannot be located.'}
                </p>
              )}
            </>
          )}
        </div>
      </div>
    );
  };

  if (!scan) {
    return (
      <p role='status' className='p-4 text-xs text-[var(--jarvis-muted)]'>
        {loading
          ? 'Loading security report…'
          : error
            ? 'Use Retry loading report to try again.'
            : 'Scan report unavailable.'}
      </p>
    );
  }

  const sources = Array.from(new Set(scan.results.map(result => result.tool)));
  const riskFindings = indexedFindings.filter(finding => finding.severity !== 'info');
  const truncated = scan.results.some(result => result.findingsTruncated);
  const emptyState = () => {
    if (scan.summary?.status === 'succeeded')
      return ['No findings', 'The last scan did not flag any findings. Scan results are advisory.'];
    if (scan.summary?.status === 'skipped')
      return ['Can’t scan this skill', 'See Scan details for the reason this scan was skipped.'];
    if (scan.summary?.status === 'partial')
      return [
        'Partial results',
        'No findings were returned by the checks that ran. See Scan details for the incomplete checks.',
      ];
    if (scan.summary?.status === 'failed') return ['Analysis failed', 'See Scan details for the scanner errors.'];
    if (scan.scanStatus === 'failed')
      return ['The scan didn’t finish', 'No report was saved. See Scan details for the failed attempt.'];
    if (scan.scanStatus === 'pending' || scan.scanStatus === 'scanning')
      return ['Waiting for results', 'The report will appear here once the scan finishes.'];
    if (scan.scanStatus === 'not_scanned')
      return [
        'Not scanned yet',
        canRequestScan ? 'Select Scan now to scan the saved skill.' : 'An editor can scan this skill.',
      ];
    return ['Scan report unavailable', 'Reopen this skill to check for a report.'];
  };
  const [emptyTitle, emptyMessage] = emptyState();
  const filtered = indexedFindings.filter(
    finding => (severity === 'all' || finding.severity === severity) && (source === 'all' || finding.tool === source),
  );
  const risks = filtered
    .filter(finding => finding.severity !== 'info')
    .sort((a, b) => SCAN_SEVERITIES.indexOf(a.severity) - SCAN_SEVERITIES.indexOf(b.severity));
  const info = filtered.filter(finding => finding.severity === 'info');
  const chipClass = (active: boolean) =>
    `rounded-full border px-3 py-1 text-xs font-medium ${active ? 'border-[var(--jarvis-primary)] bg-[var(--jarvis-primary-soft)] text-[var(--jarvis-primary-text)]' : 'border-[var(--jarvis-border)] text-[var(--jarvis-muted)] hover:bg-[var(--jarvis-card-muted)]'}`;
  return (
    <div className='space-y-4' aria-label='Security scan report'>
      {indexedFindings.length > 0 && (
        <>
          <div className='flex flex-wrap items-center gap-2'>
            <span className='text-xs text-[var(--jarvis-muted)]'>Severity</span>
            {['all', ...SCAN_SEVERITIES.filter(item => item !== 'info')].map(item => (
              <button
                key={item}
                type='button'
                aria-pressed={severity === item}
                onClick={() => setSeverity(item)}
                className={`${chipClass(severity === item)} capitalize`}
              >
                {item}
                {item !== 'all' && ` · ${riskFindings.filter(finding => finding.severity === item).length}`}
              </button>
            ))}
            <span
              aria-hidden='true'
              className='mx-2 hidden h-5 border-l border-[var(--jarvis-border-strong)] sm:inline-block'
            />
            <span className='text-xs text-[var(--jarvis-muted)]'>Source</span>
            {['all', ...sources].map(item => (
              <button
                key={item}
                type='button'
                aria-pressed={source === item}
                onClick={() => setSource(item)}
                className={chipClass(source === item)}
              >
                {item === 'all'
                  ? 'All sources'
                  : `${item} · ${riskFindings.filter(finding => finding.tool === item).length}`}
              </button>
            ))}
          </div>
          {truncated && (
            <p className='text-xs text-[var(--jarvis-warning-text)]'>
              Some findings were truncated. Filters and counts use the findings returned in this report.
            </p>
          )}
          {risks.length > 0 ? (
            <div className='space-y-3'>{risks.map(renderFinding)}</div>
          ) : (
            <div className='flex flex-wrap items-center gap-2 text-sm text-[var(--jarvis-muted)]'>
              {riskFindings.length === 0
                ? 'No risk findings in this report.'
                : 'No risk findings match the selected filters.'}
              {(severity !== 'all' || source !== 'all') && (
                <button
                  type='button'
                  onClick={() => {
                    setSeverity('all');
                    setSource('all');
                  }}
                  className='text-[var(--jarvis-primary-text)] hover:underline'
                >
                  Clear filters
                </button>
              )}
            </div>
          )}
          {info.length > 0 && (
            <div>
              <button
                type='button'
                aria-expanded={showInfo}
                onClick={() => setShowInfo(!showInfo)}
                className='text-sm text-[var(--jarvis-primary-text)]'
              >
                {showInfo ? 'Hide' : 'Show'} {info.length} informational {info.length === 1 ? 'finding' : 'findings'}{' '}
                (not counted)
              </button>
              {showInfo && <div className='mt-3 space-y-3'>{info.map(renderFinding)}</div>}
            </div>
          )}
        </>
      )}
      {indexedFindings.length === 0 && (
        <div role='status' className='rounded-xl border border-dashed border-[var(--jarvis-border)] p-8 text-center'>
          <h3 className='font-semibold text-[var(--jarvis-text-strong)]'>{emptyTitle}</h3>
          <p className='mt-2 text-sm text-[var(--jarvis-muted)]'>{emptyMessage}</p>
        </div>
      )}
      {(scan.job || scan.results.length > 0) && (
        <details
          open={detailsOpen}
          onToggle={event => setDetailsOpen(event.currentTarget.open)}
          className='overflow-hidden rounded-xl border border-[var(--jarvis-border)]'
        >
          <summary className='cursor-pointer px-4 py-3 text-sm font-semibold text-[var(--jarvis-text-strong)]'>
            Scan details
            {scan.results.length > 0 &&
              ` · ${scan.results.filter(result => result.status === 'succeeded').length} of ${scan.results.length} ${scan.results.length === 1 ? 'scanner' : 'scanners'} succeeded`}
          </summary>
          {scan.job && (
            <p className='border-t border-[var(--jarvis-border)] px-4 py-3 text-xs text-[var(--jarvis-muted)]'>
              Job: {scan.job.triggerType} · {scan.job.status} · requested {formatScanDate(scan.job.requestedAt)}
              {scan.job.startedAt && ` · started ${formatScanDate(scan.job.startedAt)}`}
              {scan.job.finishedAt && ` · finished ${formatScanDate(scan.job.finishedAt)}`}
              {` · ${scan.job.attemptCount} ${scan.job.attemptCount === 1 ? 'attempt' : 'attempts'}`}
              {scan.job.errorCode && ` · ${scan.job.errorCode}`}
            </p>
          )}
          {scan.results.map((result, toolIndex) => (
            <section key={`${toolIndex}-${result.tool}`} className='border-t border-[var(--jarvis-border)] p-4'>
              <div className='grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-6 gap-y-2 md:grid-cols-[190px_100px_minmax(0,1fr)]'>
                <h3 className='min-w-0 break-words text-sm font-semibold text-[var(--jarvis-text-strong)]'>
                  {result.tool}
                </h3>
                <span
                  className={`justify-self-start rounded-md px-2 py-1 text-xs font-semibold capitalize ${result.status === 'succeeded' ? 'bg-[var(--jarvis-success-soft)] text-[var(--jarvis-success-text)]' : result.status === 'failed' ? 'bg-[var(--jarvis-danger-soft)] text-[var(--jarvis-danger-text)]' : 'bg-[var(--jarvis-warning-soft)] text-[var(--jarvis-warning-text)]'}`}
                >
                  {result.status}
                </span>
                <div className='col-span-2 flex min-w-0 items-center gap-x-3 overflow-x-auto whitespace-nowrap font-mono text-xs text-[var(--jarvis-muted)] md:col-span-1'>
                  {result.llmModelId && (
                    <span tabIndex={0} title={result.llmModelId} className='min-w-[6rem] flex-1 truncate'>
                      {result.llmModelId}
                    </span>
                  )}
                  {result.llmProviderType && <span className='shrink-0'>{result.llmProviderType}</span>}
                  {result.inputTokens !== null && (
                    <span className='shrink-0'>{result.inputTokens.toLocaleString()} input tokens</span>
                  )}
                  {result.outputTokens !== null && (
                    <span className='shrink-0'>{result.outputTokens.toLocaleString()} output tokens</span>
                  )}
                  {result.durationMs !== null && (
                    <span className='shrink-0'>{(result.durationMs / 1000).toLocaleString()} s</span>
                  )}
                  <span className='shrink-0' title={`Scanned ${formatScanDate(result.scannedAt)}`}>
                    {result.toolVersion}
                  </span>
                </div>
              </div>
              {result.outdatedTool && (
                <p className='mt-2 text-xs text-[var(--jarvis-warning-text)]'>
                  This tool report uses an earlier tool or configuration version.
                </p>
              )}
              {result.statusReason && (
                <p className='mt-2 text-xs text-[var(--jarvis-warning-text)]'>
                  {SCAN_REASON_LABELS[result.statusReason] ?? result.statusReason}
                </p>
              )}
              {result.errorMessage && (
                <p className='mt-2 whitespace-pre-wrap break-words text-xs text-[var(--jarvis-danger)]'>
                  {result.errorMessage}
                </p>
              )}
              {result.findingsTruncated && (
                <p className='mt-2 text-xs text-[var(--jarvis-warning-text)]'>
                  Findings were truncated. Only the first 200 findings are included in this tool report.
                </p>
              )}
            </section>
          ))}
          <p className='border-t border-[var(--jarvis-border)] px-4 py-3 text-xs text-[var(--jarvis-muted)]'>
            Findings are advisory and may include false positives.
          </p>
        </details>
      )}
    </div>
  );
};

export default SkillSecurityPanel;
