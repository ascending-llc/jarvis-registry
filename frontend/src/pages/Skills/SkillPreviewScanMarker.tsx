import { type ReactNode, useId, useState } from 'react';
import SkillScanFindingCard from './SkillScanFindingCard';
import {
  compareScanFindingSeverity,
  groupScanFindingsByLocation,
  type LocatedScanFinding,
  SCAN_SEVERITY_COLORS,
} from './skillScan';
import { highlightCode } from './syntaxHighlighting';

type MarkerProps = {
  findings: LocatedScanFinding[];
  children: ReactNode;
  onShowFinding?: (key: string) => void;
  codeSurface?: { startsBlock: boolean; endsBlock: boolean };
  expansion?: { open: boolean; onToggle: () => void };
};

const codeSurfaceClass = ({ startsBlock, endsBlock }: NonNullable<MarkerProps['codeSurface']>) =>
  `bg-[var(--jarvis-card-muted)] px-4 ${startsBlock ? 'rounded-t-lg pt-4' : ''} ${endsBlock ? 'rounded-b-lg pb-4' : ''}`;

export const SkillPreviewScanMarker = ({ findings, children, onShowFinding, codeSurface, expansion }: MarkerProps) => {
  const [locallyExpanded, setLocallyExpanded] = useState(false);
  const expanded = expansion?.open ?? locallyExpanded;
  const detailsId = useId();
  const orderedFindings = [...findings].sort((a, b) => compareScanFindingSeverity(a.finding, b.finding));
  const highest = orderedFindings[0]?.finding.severity;
  if (!highest) return <>{children}</>;

  const color = SCAN_SEVERITY_COLORS[highest];
  const label = findings.length === 1 ? findings[0].finding.title : `${findings.length} findings`;
  return (
    <div className='min-w-0'>
      <div
        className={`flow-root min-w-0 ${codeSurface ? codeSurfaceClass(codeSurface) : 'rounded-sm'}`}
        style={
          expanded
            ? {
                backgroundColor: `color-mix(in srgb, ${color} 10%, ${codeSurface ? 'var(--jarvis-card-muted)' : 'transparent'})`,
              }
            : undefined
        }
      >
        {/* Keep the static vertical position of this line, aligned to the preview gutter on the left. */}
        <div className='absolute left-1.5 flex h-6 items-stretch sm:left-2' style={{ color }}>
          <span aria-hidden='true' className='w-[3px] rounded-full bg-current' />
          <button
            type='button'
            aria-label={`${highest} severity: ${label}`}
            aria-expanded={expanded}
            aria-controls={detailsId}
            title={label}
            onClick={expansion?.onToggle ?? (() => setLocallyExpanded(value => !value))}
            className='group grid h-6 w-5 place-items-center rounded focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--jarvis-primary)]'
          >
            <span
              aria-hidden='true'
              className={`h-2.5 w-2.5 rounded-full border-2 border-[color:var(--jarvis-card)] bg-current ring-1 ring-current transition-transform group-hover:scale-125 ${expanded ? 'scale-125' : ''}`}
            />
          </button>
        </div>
        {children}
      </div>
      <div id={detailsId} hidden={!expanded} className='mb-2 mt-1.5 max-w-[560px] space-y-2 font-sans'>
        {expanded &&
          orderedFindings.map(({ finding }) => (
            <SkillScanFindingCard key={finding.key} finding={finding} onShowFinding={onShowFinding} />
          ))}
      </div>
    </div>
  );
};

type TextProps = Pick<MarkerProps, 'findings' | 'onShowFinding'> & {
  text: string;
  firstLine: number;
  language?: string | null;
  wrap?: boolean;
  codeBlock?: boolean;
};

// Keep consecutive unmarked lines in a single syntax highlighted segment.
export const SkillPreviewScanText = ({
  text,
  firstLine,
  findings,
  language,
  wrap,
  codeBlock,
  onShowFinding,
}: TextProps) => {
  const [expandedLines, setExpandedLines] = useState<ReadonlySet<number>>(() => new Set());
  const toggleLine = (line: number) => {
    setExpandedLines(current => {
      const next = new Set(current);
      if (next.has(line)) next.delete(line);
      else next.add(line);
      return next;
    });
  };
  const lines = text.split(/\r\n|\n/);
  const lastLine = firstLine + lines.length - 1;
  const groups = groupScanFindingsByLocation(findings);

  const renderText = (start: number, end: number, surface?: MarkerProps['codeSurface']) => (
    <pre
      key={`text-${start}`}
      className={`skill-syntax-highlight overflow-x-auto font-mono text-[12.5px] leading-6 ${wrap ? 'whitespace-pre-wrap' : 'whitespace-pre'} ${surface ? codeSurfaceClass(surface) : ''}`}
      dangerouslySetInnerHTML={{
        __html: highlightCode(
          lines
            .slice(start, end)
            .map(line => line || '\u00a0')
            .join('\n'),
          language,
        ),
      }}
    />
  );
  let nextLine = 0;
  let startsBlock = true;
  const sections: ReactNode[] = [];
  for (const {
    range: [start, end],
    findings: entries,
  } of groups) {
    if (end < firstLine || start > lastLine) continue;
    const line = Math.max(start, firstLine) - firstLine;
    if (line > nextLine) {
      sections.push(renderText(nextLine, line, codeBlock ? { startsBlock, endsBlock: false } : undefined));
      startsBlock = false;
    }
    const segmentEnd = line + 1;
    const expanded = expandedLines.has(line);
    sections.push(
      <SkillPreviewScanMarker
        key={line}
        findings={entries}
        onShowFinding={onShowFinding}
        expansion={codeBlock ? { open: expanded, onToggle: () => toggleLine(line) } : undefined}
        codeSurface={codeBlock ? { startsBlock, endsBlock: expanded || segmentEnd === lines.length } : undefined}
      >
        {renderText(line, segmentEnd)}
      </SkillPreviewScanMarker>,
    );
    nextLine = segmentEnd;
    startsBlock = expanded;
  }
  if (nextLine < lines.length) {
    sections.push(renderText(nextLine, lines.length, codeBlock ? { startsBlock, endsBlock: true } : undefined));
  }
  return <>{sections}</>;
};
