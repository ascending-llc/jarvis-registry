import type React from 'react';
import { useEffect, useId, useRef, useState } from 'react';

type IconButtonSize = 'header' | 'card';
type IconButtonVariant = 'default' | 'primary' | 'solid';

interface IconButtonProps {
  ariaLabel: string;
  tooltip: string;
  children: React.ReactNode;
  onClick?: (e: React.MouseEvent<HTMLButtonElement>) => void;
  active?: boolean;
  disabled?: boolean;
  spinning?: boolean;
  className?: string;
  size?: IconButtonSize;
  variant?: IconButtonVariant;
  as?: 'button' | 'span';
  tooltipVisible?: boolean;
  onMouseEnter?: () => void;
  onMouseLeave?: () => void;
  onFocus?: () => void;
  onBlur?: () => void;
}

const IconButton: React.FC<IconButtonProps> = ({
  ariaLabel,
  tooltip,
  children,
  onClick,
  active = false,
  disabled = false,
  spinning = false,
  className = '',
  size = 'header',
  variant = 'default',
  as = 'button',
  tooltipVisible,
  onMouseEnter,
  onMouseLeave,
  onFocus,
  onBlur,
}) => {
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const spanRef = useRef<HTMLSpanElement | null>(null);
  const tooltipRef = useRef<HTMLSpanElement | null>(null);
  const [tooltipPlacement, setTooltipPlacement] = useState<'top' | 'bottom'>('bottom');

  const controlledTooltip = typeof tooltipVisible === 'boolean';
  const uniqueId = useId();
  const tooltipId = `icon-tooltip-${
    ariaLabel
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '') || 'button'
  }-${uniqueId}`;

  useEffect(() => {
    const updatePlacement = () => {
      const triggerElement = as === 'span' ? spanRef.current : buttonRef.current;

      if (!triggerElement || !tooltipRef.current) {
        return;
      }

      const triggerRect = triggerElement.getBoundingClientRect();
      const tooltipRect = tooltipRef.current.getBoundingClientRect();
      const spaceBelow = window.innerHeight - triggerRect.bottom;
      const requiredSpace = tooltipRect.height + 12;

      setTooltipPlacement(spaceBelow < requiredSpace ? 'top' : 'bottom');
    };

    updatePlacement();
    window.addEventListener('resize', updatePlacement);
    window.addEventListener('scroll', updatePlacement, true);

    return () => {
      window.removeEventListener('resize', updatePlacement);
      window.removeEventListener('scroll', updatePlacement, true);
    };
  }, [tooltip, tooltipVisible]);

  const wrapperClasses =
    size === 'card'
      ? `group/icon-btn relative inline-flex h-[26px] w-[26px] items-center justify-center rounded-md text-[var(--jarvis-icon)] transition-all duration-150 hover:bg-[var(--jarvis-primary-soft)] hover:text-[var(--jarvis-icon-hover)] focus:outline-none focus:ring-2 focus:ring-violet-500/40 ${className}`
      : `group/icon-btn relative inline-flex h-9 w-9 items-center justify-center rounded-lg border transition-all duration-150 focus:outline-none focus:ring-2 focus:ring-violet-500/40 ${
          active
            ? 'border-[var(--jarvis-primary)] bg-[var(--jarvis-primary-soft)] text-[var(--jarvis-primary-text)] hover:border-[var(--jarvis-primary-hover)] hover:bg-[var(--jarvis-primary-soft-hover)] hover:text-[var(--jarvis-primary-text-hover)]'
            : variant === 'primary'
              ? 'border-[var(--jarvis-primary)] bg-[var(--jarvis-primary-soft)] text-[var(--jarvis-primary-text)] hover:border-[var(--jarvis-primary-hover)] hover:bg-[var(--jarvis-primary-soft-hover)] hover:text-[var(--jarvis-primary-text-hover)]'
              : variant === 'solid'
                ? 'border-transparent bg-[var(--jarvis-primary)] text-white hover:bg-[var(--jarvis-primary-hover)] shadow-sm'
                : 'border-[color:var(--jarvis-border)] bg-[var(--jarvis-input-bg)] text-[var(--jarvis-icon)] hover:border-[color:var(--jarvis-border-strong)] hover:bg-[var(--jarvis-primary-soft)] hover:text-[var(--jarvis-icon-hover)]'
        } ${disabled ? 'cursor-not-allowed opacity-60' : ''} ${className}`;

  const tooltipClasses = `pointer-events-none absolute left-1/2 z-20 -translate-x-1/2 transition-opacity duration-100 ${
    tooltipPlacement === 'top' ? 'bottom-full mb-2' : 'top-full mt-2'
  } ${
    controlledTooltip
      ? tooltipVisible
        ? 'opacity-100 delay-200'
        : 'opacity-0 delay-0'
      : 'opacity-0 delay-0 group-hover/icon-btn:opacity-100 group-hover/icon-btn:delay-200'
  }`;

  const tooltipArrowClasses =
    tooltipPlacement === 'top'
      ? 'absolute left-1/2 bottom-0 h-2 w-2 -translate-x-1/2 translate-y-1 rotate-45 rounded-[1px] bg-[var(--jarvis-tooltip-bg)]'
      : 'absolute left-1/2 top-0 h-2 w-2 -translate-x-1/2 -translate-y-1 rotate-45 rounded-[1px] bg-[var(--jarvis-tooltip-bg)]';

  const content = (
    <>
      <span className={spinning ? 'animate-spin' : ''}>{children}</span>
      <span id={tooltipId} role='tooltip' ref={tooltipRef} className={tooltipClasses}>
        <span className='relative block whitespace-nowrap rounded-md bg-[var(--jarvis-tooltip-bg)] px-2.5 py-1 text-[11px] font-medium text-[var(--jarvis-tooltip-text)] shadow-lg'>
          <span className={tooltipArrowClasses} />
          <span className='relative z-10'>{tooltip}</span>
        </span>
      </span>
    </>
  );

  if (as === 'span') {
    return (
      <span
        ref={spanRef}
        aria-label={ariaLabel}
        aria-describedby={tooltipId}
        className={wrapperClasses}
        onMouseEnter={onMouseEnter}
        onMouseLeave={onMouseLeave}
        onFocus={onFocus}
        onBlur={onBlur}
      >
        {content}
      </span>
    );
  }

  return (
    <button
      ref={buttonRef}
      type='button'
      aria-label={ariaLabel}
      aria-describedby={tooltipId}
      onClick={onClick}
      disabled={disabled}
      className={wrapperClasses}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
      onFocus={onFocus}
      onBlur={onBlur}
    >
      {content}
    </button>
  );
};

export default IconButton;
