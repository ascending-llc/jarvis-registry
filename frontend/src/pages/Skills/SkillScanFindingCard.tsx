import { ArrowRightIcon } from '@heroicons/react/24/outline';
import {
  formatReportedScanLines,
  type IndexedScanFinding,
  SCAN_SEVERITY_CLASSES,
  SCAN_SEVERITY_COLORS,
} from './skillScan';

type Props = { finding: IndexedScanFinding; onShowFinding?: (key: string) => void };

const SkillScanFindingCard = ({ finding, onShowFinding }: Props) => {
  const reportedLines = formatReportedScanLines(finding);
  return (
    <div
      className='rounded-lg border border-[color:var(--jarvis-border-strong)] border-l-[3px] bg-[var(--jarvis-surface)] px-3 py-2.5 font-sans text-xs leading-relaxed shadow-lg'
      style={{ borderLeftColor: SCAN_SEVERITY_COLORS[finding.severity] }}
    >
      <div className='flex items-start justify-between gap-3'>
        <div className='flex min-w-0 flex-1 flex-wrap items-start gap-x-2 gap-y-1'>
          <span className={`rounded px-2 py-0.5 font-semibold capitalize ${SCAN_SEVERITY_CLASSES[finding.severity]}`}>
            {finding.severity}
          </span>
          <strong className='min-w-0 break-words text-[13px] text-[var(--jarvis-text-strong)]'>{finding.title}</strong>
        </div>
        {onShowFinding && (
          <button
            type='button'
            className='inline-flex shrink-0 items-center gap-1 py-0.5 text-right font-medium text-[var(--jarvis-primary-text)] hover:underline'
            onClick={() => onShowFinding(finding.key)}
          >
            Open in Security <ArrowRightIcon aria-hidden='true' className='h-3 w-3' />
          </button>
        )}
      </div>
      <p className='mt-1.5 break-words text-[var(--jarvis-muted)]'>
        <span className='capitalize'>{finding.category.replaceAll('_', ' ')}</span> · {finding.tool}
        {reportedLines && ` · Reported at ${reportedLines}`}
      </p>
    </div>
  );
};

export default SkillScanFindingCard;
