import type React from 'react';

interface NodeStatusDotProps {
  colorClass: string;
  isRunning: boolean;
}

export const NodeStatusDot: React.FC<NodeStatusDotProps> = ({ colorClass, isRunning }) => (
  <div
    aria-hidden={isRunning ? undefined : true}
    role={isRunning ? 'status' : undefined}
    className={`node-dot ${colorClass}${isRunning ? ' workflow-status-running workflow-status-ripple' : ''}`}
  >
    {isRunning && <span className='sr-only'>Running</span>}
  </div>
);
