import type React from 'react';

const AgentIcon: React.FC<React.SVGProps<SVGSVGElement>> = props => {
  return (
    <svg viewBox='0 0 24 24' fill='none' xmlns='http://www.w3.org/2000/svg' {...props}>
      <path
        d='M12 2a3 3 0 0 0-3 3v1H6a3 3 0 0 0-3 3v8a3 3 0 0 0 3 3h12a3 3 0 0 0 3-3V9a3 3 0 0 0-3-3h-3V5a3 3 0 0 0-3-3Z'
        stroke='currentColor'
        strokeWidth='1.5'
        strokeLinecap='round'
      />
        <circle cx='9' cy='13' r='1.5' fill='currentColor' />
        <circle cx='15' cy='13' r='1.5' fill='currentColor' />
        <path d='M9 17h6' stroke='currentColor' strokeWidth='1.5' strokeLinecap='round' />
    </svg>
  );
};

export default AgentIcon;
