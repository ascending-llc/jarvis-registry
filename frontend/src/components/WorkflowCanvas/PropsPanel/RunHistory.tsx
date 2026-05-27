import type React from 'react';
import type { PanelMode, RunEntry } from '../types';
import { useRunHistory } from './hooks/useRunHistory';

// ─── visual config ────────────────────────────────────────────────────────────

const RUN_COLORS: Record<string, string> = {
  ok: 'var(--jarvis-success)',
  fail: 'var(--jarvis-danger)',
  live: 'var(--jarvis-warning)',
  paused: 'var(--jarvis-subtle)',
};
const RUN_GLOWS: Record<string, string | undefined> = {
  ok: 'var(--jarvis-success-soft)',
  fail: 'var(--jarvis-danger-soft)',
  live: 'var(--jarvis-warning-soft)',
  paused: undefined,
};
const ACTION_S: Record<string, { color: string; borderColor: string }> = {
  pause: { color: 'var(--jarvis-warning)', borderColor: 'rgba(245,158,11,.3)' },
  cancel: { color: 'var(--jarvis-danger)', borderColor: 'rgba(239,68,68,.25)' },
  resume: { color: 'var(--jarvis-success)', borderColor: 'rgba(16,185,129,.3)' },
  retry: { color: '#38bdf8', borderColor: 'var(--jarvis-blue-soft)' },
};
const ACTION_LABELS: Record<string, string> = { pause: '⏸', cancel: '✕', resume: '▶', retry: '↻' };

// ─── sub-components ───────────────────────────────────────────────────────────

const RunRow: React.FC<{ run: RunEntry }> = ({ run }) => (
  <div className='flex items-start gap-2 bg-[var(--jarvis-card-muted)] border border-[var(--jarvis-border)] rounded-lg p-2.5 mb-1.5'>
    <div
      className='w-[7px] h-[7px] rounded-full shrink-0 mt-[3px]'
      style={{
        background: RUN_COLORS[run.status],
        boxShadow: RUN_GLOWS[run.status] ? `0 0 0 2px ${RUN_GLOWS[run.status]}` : 'none',
        animation: run.status === 'live' ? 'pulse 1.2s infinite' : 'none',
      }}
    />
    <div className='flex-1 min-w-0'>
      <div className='font-mono text-[11px] font-medium text-[var(--jarvis-text)] mb-0.5'>{run.id}</div>
      <div className='text-[11px] text-[var(--jarvis-subtle)]'>{run.time}</div>
      {run.err && (
        <span className='inline-block mt-1 text-[9px] font-mono px-1.5 py-0.5 rounded bg-[var(--jarvis-danger-soft)] border border-[rgba(239,68,68,.25)] text-[var(--jarvis-danger-text)]'>
          {run.err}
        </span>
      )}
    </div>
    {run.dur && <div className='font-mono text-[11px] text-[var(--jarvis-subtle)] shrink-0'>{run.dur}</div>}
    {run.actions && run.actions.length > 0 && (
      <div className='flex flex-col gap-1 shrink-0'>
        {run.actions.map(a => {
          const s = ACTION_S[a];
          return (
            <button
              key={a}
              className={`action-btn action-btn--${a} rounded-md border px-2 py-0.5 text-[10px] font-medium cursor-pointer transition-colors`}
              style={{ borderColor: s?.borderColor, color: s?.color, fontFamily: 'Inter,sans-serif' }}
            >
              {ACTION_LABELS[a]}
            </button>
          );
        })}
      </div>
    )}
  </div>
);

interface RunHistoryProps {
  panelMode: PanelMode;
}

const RunHistory: React.FC<RunHistoryProps> = ({ panelMode }) => {
  const { runs, loading, error, showAllWorkflowRuns, workflowId, selectedNodeId, selectedNodeLabel } = useRunHistory({
    panelMode,
  });

  const heading =
    panelMode === 'workflow'
      ? 'Workflow — run history'
      : selectedNodeLabel
        ? `${selectedNodeLabel} — run history`
        : 'Run history';

  return (
    <div className='px-4 py-3'>
      <div className='font-mono text-[10px] font-bold tracking-wide uppercase text-[var(--jarvis-subtle)] mb-1.5'>
        {heading}
      </div>
      {!workflowId ? (
        <p className='text-xs text-[var(--jarvis-subtle)] text-center py-6'>Save the workflow to view run history</p>
      ) : loading ? (
        <div className='flex justify-center py-6'>
          <div className='h-5 w-5 animate-spin rounded-full border-b-2 border-[var(--jarvis-primary)]' />
        </div>
      ) : error ? (
        <p className='text-xs text-[var(--jarvis-danger-text)] text-center py-6'>{error}</p>
      ) : panelMode === 'node' && !selectedNodeId ? (
        <p className='text-xs text-[var(--jarvis-subtle)] text-center py-6'>Click a node to view its run history</p>
      ) : runs.length === 0 ? (
        <p className='text-xs text-[var(--jarvis-subtle)] text-center py-6'>
          {panelMode === 'workflow' ? 'No runs yet' : 'No runs for this node yet'}
        </p>
      ) : (
        <>
          {showAllWorkflowRuns && (
            <p className='text-[10px] text-[var(--jarvis-muted)] mb-2'>
              No node-specific runs found — showing all workflow runs
            </p>
          )}
          {runs.map((r, i) => (
            <RunRow key={`${r.id}-${i}`} run={r} />
          ))}
        </>
      )}
    </div>
  );
};

export { RunHistory };
export default RunHistory;
