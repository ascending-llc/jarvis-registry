import { ClockIcon } from '@heroicons/react/24/outline';
import type { SkillScanBriefResponse } from '@/services/skill/scanTypes';
import { getHighestRisk, getRiskCount, getScanPresentation } from './skillScan';

type Props = { summary: SkillScanBriefResponse | null | undefined };

const SkillScanBadge = ({ summary }: Props) => {
  const { label, className, notes } = getScanPresentation(summary);
  const highest = summary && getHighestRisk(summary.severityCounts);
  const showRisk =
    summary?.scanStatus === 'completed' && (summary.status === 'succeeded' || summary.status === 'partial') && highest;
  const count = highest && summary ? (summary.severityCounts[highest] ?? 0) : 0;
  const remaining = summary ? getRiskCount(summary.severityCounts) - count : 0;
  const skipped = summary?.scanStatus === 'completed' && summary.status === 'skipped';
  const notScanned = summary?.scanStatus === 'not_scanned';
  const active = summary?.scanStatus === 'scanning';
  const failed =
    summary?.scanStatus === 'failed' || (summary?.scanStatus === 'completed' && summary.status === 'failed');
  const tooltip = notes.join(' · ');
  return (
    <span className='inline-flex min-w-0 items-center gap-2 text-left'>
      <span
        className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs font-medium ${className} ${notScanned ? 'border-dashed' : skipped ? 'border-dotted' : 'border-current'}`}
      >
        {active ? (
          <span
            aria-hidden='true'
            className='h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent'
          />
        ) : showRisk ? (
          <span aria-hidden='true' className='h-1.5 w-1.5 flex-none rounded-full bg-current' />
        ) : null}
        {showRisk && highest ? (
          <>
            {highest.charAt(0).toUpperCase() + highest.slice(1)} · {count}
            {remaining > 0 && <span className='text-[var(--jarvis-muted)]'>+{remaining}</span>}
          </>
        ) : skipped ? (
          'Can’t scan'
        ) : failed ? (
          'Scan failed'
        ) : (
          label
        )}
        {summary?.status === 'partial' && showRisk && <span className='text-[var(--jarvis-muted)]'>partial</span>}
      </span>
      {(summary?.stale || summary?.outdatedTool) && (
        <ClockIcon className='h-4 w-4 shrink-0 text-[var(--jarvis-warning-text)]' aria-label={tooltip} />
      )}
    </span>
  );
};

export default SkillScanBadge;
