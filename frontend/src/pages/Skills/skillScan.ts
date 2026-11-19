import type {
  SkillScanBriefResponse,
  SkillScanFindingResponse,
  SkillScanReason,
  SkillScanResponse,
  SkillScanSeverity,
  SkillScanSeverityCounts,
} from '@/services/skill/scanTypes';

export const SKILL_SCAN_POLL_INTERVAL_MS = 5_000;
export const SCAN_SEVERITIES: SkillScanSeverity[] = ['critical', 'high', 'medium', 'low', 'info'];
export const SCAN_SEVERITY_CLASSES: Record<SkillScanSeverity, string> = {
  critical: 'bg-[var(--jarvis-danger-soft)] text-[var(--jarvis-danger-text)]',
  high: 'bg-[var(--jarvis-orange-soft)] text-[var(--jarvis-orange)]',
  medium: 'bg-[var(--jarvis-warning-soft)] text-[var(--jarvis-warning-text)]',
  low: 'bg-[var(--jarvis-card-muted)] text-[var(--jarvis-muted)]',
  info: 'bg-[var(--jarvis-card-muted)] text-[var(--jarvis-muted)]',
};
export const SCAN_REASON_LABELS: Record<SkillScanReason, string> = {
  llm_unavailable: 'LLM analysis was unavailable. Existing findings remain available.',
  llm_failed: 'The LLM analysis failed. Existing findings remain available.',
  llm_skipped_size_limit: 'The content exceeded the size limit for LLM analysis.',
  analysis_incomplete: 'The tool completed only part of its analysis.',
  chat_supporting_files_unsupported:
    'This skill includes a supporting file stored by Jarvis Chat that the scanner cannot access.',
  tool_error: 'The scanning tool could not complete its analysis.',
  timeout: 'The scanning tool exceeded its time limit.',
  parse_error: 'The scanning tool returned a result that could not be parsed.',
};

export type IndexedScanFinding = SkillScanFindingResponse & { key: string; toolIndex: number };
export type SkillScanTarget = {
  findingKey: string;
  path: string;
  startLine: number | null;
  endLine: number | null;
  scanVersion: number;
};

export const isScanActive = (status?: string): boolean => status === 'pending' || status === 'scanning';

export const getRiskCount = (counts: SkillScanSeverityCounts): number =>
  SCAN_SEVERITIES.filter(severity => severity !== 'info').reduce(
    (total, severity) => total + (counts[severity] ?? 0),
    0,
  );

export const getHighestRisk = (counts: SkillScanSeverityCounts) =>
  SCAN_SEVERITIES.find(severity => severity !== 'info' && (counts[severity] ?? 0) > 0);

export const formatRiskBreakdown = (counts: SkillScanSeverityCounts) =>
  SCAN_SEVERITIES.filter(severity => severity !== 'info' && (counts[severity] ?? 0) > 0)
    .map(severity => `${counts[severity]} ${severity}`)
    .join(' · ');

export const toScanBrief = (scan: SkillScanResponse): SkillScanBriefResponse => ({
  scanStatus: scan.scanStatus,
  status: scan.summary?.status ?? null,
  maxSeverity: scan.summary?.maxSeverity ?? null,
  severityCounts: scan.summary?.severityCounts ?? {},
  stale: scan.stale,
  outdatedTool: scan.outdatedTool,
  scannedAt: scan.summary?.scannedAt ?? null,
});

const neutralClass = 'bg-[var(--jarvis-card-muted)] text-[var(--jarvis-muted)]';

export const getScanPresentation = (brief: SkillScanBriefResponse | null | undefined) => {
  let label = 'Scan status unavailable';
  let className = neutralClass;
  const notes: string[] = [];
  if (!brief) return { label, className, notes };

  if (brief.stale) notes.push('Content changed');
  if (brief.outdatedTool) notes.push('Configuration changed');
  if (brief.status === 'partial') notes.push('Partial analysis');

  const taskLabels: Record<string, string> = {
    not_scanned: 'Not scanned',
    pending: 'Queued',
    scanning: 'Scanning',
    failed: 'Scan failed',
  };
  if (taskLabels[brief.scanStatus]) {
    label = taskLabels[brief.scanStatus];
    if (isScanActive(brief.scanStatus)) className = 'bg-[var(--jarvis-primary-soft)] text-[var(--jarvis-primary-text)]';
    if (brief.scanStatus === 'failed') className = SCAN_SEVERITY_CLASSES.critical;
    if (brief.status && brief.scannedAt) notes.push(`Previous report · ${getRiskCount(brief.severityCounts)} findings`);
    return { label, className, notes };
  }

  if (brief.scanStatus !== 'completed') return { label, className, notes };
  if (brief.status === 'skipped') return { label: 'Skipped', className, notes };
  if (brief.status === 'failed') return { label: 'Analysis failed', className: SCAN_SEVERITY_CLASSES.critical, notes };
  if (brief.status !== 'succeeded' && brief.status !== 'partial') return { label, className, notes };

  const highest = getHighestRisk(brief.severityCounts);
  if (highest) {
    const count = brief.severityCounts[highest] ?? 0;
    const remaining = getRiskCount(brief.severityCounts) - count;
    label = `${count} ${highest}${remaining > 0 ? ` · +${remaining}` : ''}`;
    className = SCAN_SEVERITY_CLASSES[highest];
  } else {
    label = brief.status === 'partial' ? 'Partial analysis' : 'No findings';
  }
  return { label, className, notes: notes.filter(note => note !== label) };
};

export const getScanHttpStatus = (error: unknown): number | undefined => {
  if (error && typeof error === 'object' && 'httpStatus' in error && typeof error.httpStatus === 'number')
    return error.httpStatus;
  return undefined;
};

export const getScanErrorMessage = (error: unknown, fallback: string): string => {
  if (error && typeof error === 'object' && 'detail' in error) {
    const detail = error.detail;
    if (typeof detail === 'string' && detail.trim()) return detail;
    if (Array.isArray(detail)) return 'Invalid request. Refresh the page and try again.';
    if (detail && typeof detail === 'object' && 'message' in detail && typeof detail.message === 'string')
      return detail.message;
  }
  return error instanceof Error ? error.message : fallback;
};

export const indexScanFindings = (scan: SkillScanResponse | null): IndexedScanFinding[] =>
  scan?.results.flatMap((result, toolIndex) =>
    result.findings.map((finding, index) => ({ ...finding, key: `${toolIndex}-${index}`, toolIndex })),
  ) ?? [];

export const resolveScanFilePath = (path: string | null, availablePaths: readonly string[]): string | null => {
  if (!path) return null;
  const normalized = path.replace(/\\/g, '/').replace(/^\.\//, '');
  if (normalized.startsWith('/') || normalized.includes(':') || normalized.split('/').includes('..')) return null;
  return availablePaths.find(available => available.replace(/\\/g, '/').replace(/^\.\//, '') === normalized) ?? null;
};

export const formatScanDate = (value: string | null | undefined): string => {
  if (!value) return 'Unknown time';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'Unknown time' : date.toLocaleString();
};

export const getScanStatusDetails = (summary: SkillScanBriefResponse | null | undefined): string => {
  const { label, notes } = getScanPresentation(summary);
  const breakdown = summary ? formatRiskBreakdown(summary.severityCounts) : '';
  return [label, breakdown, summary?.scannedAt && `Last scanned ${formatScanDate(summary.scannedAt)}`, ...notes]
    .filter(Boolean)
    .join(' · ');
};
