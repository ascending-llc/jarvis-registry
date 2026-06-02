import type React from 'react';
import { useCallback, useRef, useState } from 'react';

const MIN_W = 200;
const MAX_W = 480;
const DEFAULT_W = 264;

interface ResizablePanelProps {
  collapsed: boolean;
  onCollapsedChange: (val: boolean) => void;
  header: React.ReactNode;
  tab: 'props' | 'hist';
  onTabChange: (tab: 'props' | 'hist') => void;
  children: React.ReactNode;
}

export const ResizablePanel: React.FC<ResizablePanelProps> = ({
  collapsed,
  onCollapsedChange,
  header,
  tab,
  onTabChange,
  children,
}) => {
  const [width, setWidth] = useState(DEFAULT_W);
  const draggingRef = useRef(false);
  const startXRef = useRef(0);
  const startWRef = useRef(DEFAULT_W);

  const onResizeStart = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      draggingRef.current = true;
      startXRef.current = e.clientX;
      startWRef.current = width;

      const onMove = (mv: MouseEvent) => {
        if (!draggingRef.current) return;
        const delta = startXRef.current - mv.clientX;
        const newW = Math.min(MAX_W, Math.max(MIN_W, startWRef.current + delta));
        setWidth(newW);
      };
      const onUp = () => {
        draggingRef.current = false;
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onUp);
      };
      window.addEventListener('mousemove', onMove);
      window.addEventListener('mouseup', onUp);
    },
    [width],
  );

  const panelW = collapsed ? 0 : width;

  return (
    <div className='flex shrink-0 relative h-full'>
      {!collapsed && (
        <div
          onMouseDown={onResizeStart}
          className='w-1 cursor-col-resize shrink-0 z-10 transition-colors duration-150 hover:bg-[var(--jarvis-primary)]'
        />
      )}

      {collapsed && (
        <button
          type='button'
          onClick={() => onCollapsedChange(false)}
          title='Expand panel'
          className='absolute right-0 top-0 w-9 h-[42px] z-50 flex items-center justify-center bg-[var(--jarvis-card)] border-b border-l border-[var(--jarvis-border)] text-[var(--jarvis-subtle)] hover:text-[var(--jarvis-text-strong)] rounded-bl-md cursor-pointer shadow-sm transition-colors duration-200'
        >
          <svg width='14' height='14' fill='none' stroke='currentColor' viewBox='0 0 24 24'>
            <path strokeLinecap='round' strokeLinejoin='round' strokeWidth='2' d='M15 19l-7-7 7-7' />
          </svg>
        </button>
      )}

      <div
        className='bg-[var(--jarvis-card)] border-l border-[var(--jarvis-border)] flex flex-col overflow-hidden shrink-0 h-full transition-all duration-200 ease-out'
        style={{ width: panelW }}
      >
        {!collapsed && (
          <>
            {header}

            <div className='flex items-center border-b border-[var(--jarvis-border)] shrink-0'>
              <button
                type='button'
                onClick={() => onCollapsedChange(true)}
                title='Collapse panel'
                className='w-9 h-[42px] flex items-center justify-center bg-none border-none text-[var(--jarvis-subtle)] hover:text-[var(--jarvis-text-strong)] cursor-pointer shrink-0 transition-colors duration-200'
              >
                <svg width='14' height='14' fill='none' stroke='currentColor' viewBox='0 0 24 24'>
                  <path strokeLinecap='round' strokeLinejoin='round' strokeWidth='2' d='M9 5l7 7-7 7' />
                </svg>
              </button>

              <button
                type='button'
                className='flex-1 px-1.5 py-2.5 text-center font-sans text-[11.5px] font-medium cursor-pointer bg-none border-none transition-all duration-200'
                style={{
                  color: tab === 'props' ? 'var(--jarvis-primary-text)' : 'var(--jarvis-subtle)',
                  borderBottom: tab === 'props' ? '2px solid var(--jarvis-primary-hover)' : '2px solid transparent',
                }}
                onClick={() => onTabChange('props')}
              >
                Properties
              </button>
              <button
                type='button'
                className='flex-1 px-1.5 py-2.5 text-center font-sans text-[11.5px] font-medium cursor-pointer bg-none border-none transition-all duration-200'
                style={{
                  color: tab === 'hist' ? 'var(--jarvis-primary-text)' : 'var(--jarvis-subtle)',
                  borderBottom: tab === 'hist' ? '2px solid var(--jarvis-primary-hover)' : '2px solid transparent',
                }}
                onClick={() => onTabChange('hist')}
              >
                Run history
              </button>
            </div>
          </>
        )}

        {!collapsed && (
          <div className='flex-1 overflow-y-auto'>
            <style>{`@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}`}</style>
            {children}
          </div>
        )}
      </div>
    </div>
  );
};

export default ResizablePanel;
