import type React from 'react';
import { useId, useState } from 'react';

interface TooltipProps {
  content: string;
  children: React.ReactNode;
  className?: string;
  placement?: 'top' | 'bottom';
}

const Tooltip: React.FC<TooltipProps> = ({ content, children, className = '', placement = 'bottom' }) => {
  const [hovered, setHovered] = useState(false);
  const id = useId();

  if (!content) return <>{children}</>;

  const tooltipClasses = `pointer-events-none absolute z-[99] w-max max-w-xs whitespace-normal break-words rounded-md bg-[var(--jarvis-tooltip-bg)] px-2.5 py-1.5 text-[12px] font-medium leading-relaxed text-[var(--jarvis-tooltip-text)] shadow-lg transition-opacity duration-200 ${
    hovered ? 'opacity-100 delay-300' : 'opacity-0 delay-0'
  } ${placement === 'top' ? 'bottom-full mb-2 left-0' : 'top-full mt-2 left-0'}`;

  const arrowClasses = `absolute w-2 h-2 rotate-45 bg-[var(--jarvis-tooltip-bg)] ${
    placement === 'top' ? 'bottom-[-4px] left-4' : 'top-[-4px] left-4'
  }`;

  return (
    <div
      className={`relative inline-block w-full ${className}`}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onFocus={() => setHovered(true)}
      onBlur={() => setHovered(false)}
      aria-describedby={id}
    >
      {children}
      <div id={id} role='tooltip' className={tooltipClasses}>
        <div className={arrowClasses} />
        <span className='relative z-10 block'>{content}</span>
      </div>
    </div>
  );
};

export default Tooltip;
