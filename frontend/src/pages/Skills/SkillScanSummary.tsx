import { ShieldCheckIcon } from '@heroicons/react/24/outline';
import type { SkillScanResponse } from '@/services/skill/scanTypes';
import {
  formatRiskBreakdown,
  formatScanDate,
  getHighestRisk,
  getRiskCount,
  getScanPresentation,
  isScanActive,
  toScanBrief,
} from './skillScan';

type SkillScanSummaryProps = {
  scan: SkillScanResponse | null;
  loading: boolean;
  refreshing: boolean;
  requesting: boolean;
  error: string | null;
  requestError: string | null;
  canRequestScan: boolean;
  hasUnsavedChanges: boolean;
  busy: boolean;
  onRefresh: () => void;
  onRequestScan: () => void;
};

const SkillScanSummary = ({
  scan,
  loading,
  refreshing,
  requesting,
  error,
  requestError,
  canRequestScan,
  hasUnsavedChanges,
  busy,
  onRefresh,
  onRequestScan,
}: SkillScanSummaryProps) => {
  const presentation = getScanPresentation(scan ? toScanBrief(scan) : null);
  const summary = scan?.summary;
  const isActive = isScanActive(scan?.scanStatus);
  const isSkipped = scan?.scanStatus === 'completed' && summary?.status === 'skipped';
  const isFailed = scan?.scanStatus === 'failed' || (scan?.scanStatus === 'completed' && summary?.status === 'failed');
  const unsupported =
    isSkipped &&
    !scan?.stale &&
    scan?.results.some(result => result.statusReason === 'chat_supporting_files_unsupported');
  const highest = summary ? getHighestRisk(summary.severityCounts) : undefined;
  const showRiskBadge =
    scan?.scanStatus === 'completed' &&
    (summary?.status === 'succeeded' || summary?.status === 'partial') &&
    highest !== undefined;
  const highestCount = highest && summary ? (summary.severityCounts[highest] ?? 0) : 0;
  const remainingCount = summary ? getRiskCount(summary.severityCounts) - highestCount : 0;
  const breakdown = summary ? formatRiskBreakdown(summary.severityCounts) : '';
  let scanAction = summary ? 'Rescan' : 'Scan now';
  if (isFailed) scanAction = 'Retry scan';
  if (isActive) scanAction = presentation.label;
  if (requesting) scanAction = 'Requesting scan…';
  // The job describes the latest attempt, which may differ from the retained report.
  const reportTrigger =
    scan?.scanStatus === 'completed' && scan.job?.status === 'done'
      ? scan.job.triggerType === 'auto'
        ? 'automatic'
        : 'manual'
      : null;
  const scannedAt = summary ? new Date(summary.scannedAt) : null;
  const scannedAtLabel =
    scannedAt && !Number.isNaN(scannedAt.getTime())
      ? scannedAt.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
      : 'Unknown time';

  return (
    <section
      aria-label='Security scan summary'
      className='rounded-xl border border-[var(--jarvis-border)] bg-[var(--jarvis-card-muted)] px-4 py-3.5 sm:px-5'
    >
      <div className='flex flex-wrap items-center gap-x-5 gap-y-3'>
        <h3 className='inline-flex shrink-0 items-center gap-3 text-sm font-semibold text-[var(--jarvis-text-strong)]'>
          <ShieldCheckIcon aria-hidden='true' className='h-5 w-5 text-[var(--jarvis-primary-text)]' />
          Security scan
        </h3>
        <div className='flex min-w-0 flex-1 flex-wrap items-center gap-x-5 gap-y-2'>
          <span
            role='status'
            className={`inline-flex shrink-0 items-center gap-1.5 rounded-md border border-current px-2.5 py-1 text-xs font-semibold ${presentation.className}`}
          >
            {scan?.scanStatus === 'scanning' ? (
              <span
                aria-hidden='true'
                className='h-3 w-3 rounded-full border-2 border-current border-t-transparent motion-safe:animate-spin'
              />
            ) : showRiskBadge ? (
              <span aria-hidden='true' className='h-1.5 w-1.5 shrink-0 rounded-full bg-current' />
            ) : null}
            {showRiskBadge && highest ? (
              <>
                {highest.charAt(0).toUpperCase() + highest.slice(1)} · {highestCount}
                {remainingCount > 0 && (
                  <span className='font-medium text-[var(--jarvis-muted)]'>+{remainingCount}</span>
                )}
                {summary?.status === 'partial' && (
                  <span className='font-normal text-[var(--jarvis-muted)]'>partial</span>
                )}
              </>
            ) : loading && !scan ? (
              'Loading scan status...'
            ) : isSkipped ? (
              'Can’t scan'
            ) : (
              presentation.label
            )}
          </span>
          {breakdown && (
            <p className='text-[13px]/5 font-normal text-[var(--jarvis-text)] dark:text-[#c7cbd6]'>
              {scan?.scanStatus !== 'completed' && 'Previous report · '}
              {breakdown}
            </p>
          )}
          {summary && (
            <p
              title={`Report for version ${summary.skillVersion} · ${formatScanDate(summary.scannedAt)}`}
              className='text-[13px]/5 font-normal text-[var(--jarvis-muted)] dark:text-[#a1a8b8]'
            >
              Last scanned {scannedAtLabel}
              {reportTrigger && ` · ${reportTrigger}`}
            </p>
          )}
        </div>
        <div className='ml-auto flex shrink-0 items-center gap-2'>
          {error && (
            <button
              type='button'
              onClick={onRefresh}
              disabled={loading || refreshing || requesting}
              className='rounded-md border border-[var(--jarvis-border)] px-3 py-1.5 text-xs font-medium text-[var(--jarvis-text)] hover:bg-[var(--jarvis-card-muted)] disabled:cursor-not-allowed disabled:opacity-50'
            >
              Retry loading report
            </button>
          )}
          {canRequestScan && !unsupported && (
            <button
              type='button'
              onClick={onRequestScan}
              disabled={loading || refreshing || requesting || busy || isActive || hasUnsavedChanges}
              className='rounded-lg border border-[var(--jarvis-primary-text)] bg-[var(--jarvis-primary-soft)] px-4 py-2 text-sm font-semibold text-[var(--jarvis-primary-text)] transition hover:bg-[var(--jarvis-primary-soft-hover)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--jarvis-primary)] disabled:cursor-not-allowed disabled:opacity-50'
            >
              {scanAction}
            </button>
          )}
        </div>
      </div>
      {!canRequestScan && !unsupported && !loading && !error && (
        <p className='mt-3 text-xs text-[var(--jarvis-muted)]'>View only · editors can request a scan.</p>
      )}
      {isActive && (
        <p
          role='status'
          className='mt-3 rounded-md bg-[var(--jarvis-primary-soft)] px-3 py-2 text-xs text-[var(--jarvis-primary-text)]'
        >
          {scan?.scanStatus === 'pending' ? 'A scan is queued.' : 'A scan is running.'} Results update automatically.
          {summary && ` The visible report is from the last completed scan of version ${summary.skillVersion}.`}
        </p>
      )}
      {unsupported && (
        <p className='mt-3 text-xs text-[var(--jarvis-warning-text)]'>
          This skill includes a supporting file stored by Jarvis Chat that Registry can’t scan.
        </p>
      )}
      {scan?.stale && (
        <p className='mt-3 text-xs text-[var(--jarvis-warning-text)]'>
          Skill content has changed since this report. These findings describe the scanned version.
        </p>
      )}
      {scan?.outdatedTool && (
        <p className='mt-3 text-xs text-[var(--jarvis-warning-text)]'>
          The scanning tool or configuration has changed. Re-scan to generate a report with the current configuration.
        </p>
      )}
      {scan?.job?.rerequested && (
        <p role='status' className='mt-3 text-xs text-[var(--jarvis-muted)]'>
          Another scan was requested while this job was active. A follow-up scan is queued.
        </p>
      )}
      {hasUnsavedChanges && canRequestScan && (
        <p className='mt-3 text-xs text-[var(--jarvis-muted)]'>
          Save your changes to queue a scan. The current report describes saved content.
        </p>
      )}
      {error && (
        <p role='alert' className='mt-3 text-xs text-[var(--jarvis-danger)]'>
          {error}
        </p>
      )}
      {requestError && (
        <p role='alert' className='mt-3 text-xs text-[var(--jarvis-danger)]'>
          {requestError}
        </p>
      )}
    </section>
  );
};

export default SkillScanSummary;
