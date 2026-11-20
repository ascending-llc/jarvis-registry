import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { SkillScanBriefResponse } from '@/services/skill/scanTypes';
import SkillScanBadge from './SkillScanBadge';
import { getScanTooltip } from './skillScan';

type Props = {
  summary: SkillScanBriefResponse | null | undefined;
  skillName: string;
  onOpen: () => void;
};

const VIEWPORT_PADDING = 8;

const SkillScanBadgeButton = ({ summary, skillName, onOpen }: Props) => {
  const id = useId();
  const anchor = useRef<HTMLSpanElement>(null);
  const tooltip = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const { title, lines } = getScanTooltip(summary);
  const description = lines.join('\n');

  useLayoutEffect(() => {
    if (!open || !anchor.current || !tooltip.current) return;
    const rect = anchor.current.getBoundingClientRect();
    const { width, height } = tooltip.current.getBoundingClientRect();
    const left = Math.max(VIEWPORT_PADDING, Math.min(rect.left, window.innerWidth - width - VIEWPORT_PADDING));
    const top = rect.bottom + height + VIEWPORT_PADDING <= window.innerHeight ? rect.bottom : rect.top - height;
    tooltip.current.style.left = `${left}px`;
    tooltip.current.style.top = `${Math.max(VIEWPORT_PADDING, top)}px`;
  }, [open, title, description]);

  useEffect(() => {
    if (!open) return;
    const dismiss = () => setOpen(false);
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') dismiss();
    };
    window.addEventListener('keydown', onKeyDown);
    window.addEventListener('resize', dismiss);
    window.addEventListener('scroll', dismiss, true);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('resize', dismiss);
      window.removeEventListener('scroll', dismiss, true);
    };
  }, [open]);

  return (
    <span
      ref={anchor}
      className='w-fit max-w-full min-w-0'
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => {
        if (!anchor.current?.contains(document.activeElement) || !document.activeElement?.matches(':focus-visible')) {
          setOpen(false);
        }
      }}
    >
      <button
        type='button'
        onClick={onOpen}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        className='max-w-full rounded-md text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--jarvis-primary)]'
        aria-label={`View security scan for ${skillName}`}
        aria-describedby={id}
      >
        <SkillScanBadge summary={summary} />
      </button>
      {open ? (
        createPortal(
          <div
            id={id}
            ref={tooltip}
            role='tooltip'
            className='fixed z-50 w-[250px] max-w-[calc(100vw_-_16px)] break-words rounded-[10px] border border-[color:var(--jarvis-border-strong)] bg-[var(--jarvis-surface)] px-3.5 py-3 text-xs shadow-xl'
          >
            <p className='font-bold text-[var(--jarvis-text-strong)]'>{title}</p>
            {description && (
              <p className='mt-1.5 whitespace-pre-line leading-relaxed text-[var(--jarvis-muted)]'>{description}</p>
            )}
          </div>,
          document.body,
        )
      ) : (
        <span id={id} hidden>
          {[title, description].filter(Boolean).join('. ')}
        </span>
      )}
    </span>
  );
};

export default SkillScanBadgeButton;
