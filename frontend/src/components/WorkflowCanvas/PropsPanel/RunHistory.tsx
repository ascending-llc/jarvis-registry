import type React from 'react';
import { useEffect, useState } from 'react';
import SERVICES from '@/services';
import type { WorkflowRun } from '@/services/workflow/type';
import type { RunEntry } from '../types';

// ─── helpers ──────────────────────────────────────────────────────────────────

const STATUS_MAP: Record<WorkflowRun['status'], RunEntry['status']> = {
  running: 'live',
  pending: 'live',
  paused: 'paused',
  completed: 'ok',
  failed: 'fail',
  cancelled: 'fail',
};

const ACTIONS_BY_STATUS: Record<WorkflowRun['status'], RunEntry['actions']> = {
  running: ['pause', 'cancel'],
  pending: ['cancel'],
  paused: ['resume', 'cancel'],
  completed: [],
  failed: ['retry'],
  cancelled: ['retry'],
};

const formatDuration = (startedAt: string, finishedAt?: string): string | undefined => {
  if (!finishedAt) return undefined;
  const ms = new Date(finishedAt).getTime() - new Date(startedAt).getTime();
  if (ms < 0) return undefined;
  const secs = Math.floor(ms / 1000);
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
};

const formatTime = (iso: string): string => {
  const d = new Date(iso);
  const now = new Date();
  const isToday = d.toDateString() === now.toDateString();
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  const isYesterday = d.toDateString() === yesterday.toDateString();
  const hhmm = d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  if (isToday) return `Today ${hhmm}`;
  if (isYesterday) return `Yesterday ${hhmm}`;
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + ` ${hhmm}`;
};

const workflowRunToEntry = (run: WorkflowRun): RunEntry => ({
  id: run.id.slice(-8),
  status: STATUS_MAP[run.status] ?? 'fail',
  time: formatTime(run.startedAt),
  dur: formatDuration(run.startedAt, run.finishedAt),
  err: run.errorSummary ?? undefined,
  actions: ACTIONS_BY_STATUS[run.status] ?? [],
});

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
              className='action-btn rounded-md border px-2 py-0.5 text-[10px] font-medium cursor-pointer transition-colors'
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

// ─── main component ───────────────────────────────────────────────────────────

interface RunHistoryProps {
  workflowId?: string;
  selectedNodeId: string | undefined;
  selectedNodeLabel: string | undefined;
}

const RunHistory: React.FC<RunHistoryProps> = ({ workflowId, selectedNodeId, selectedNodeLabel }) => {
  const [runs, setRuns] = useState<RunEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!workflowId) return;
    let cancelled = false;

    const fetchRuns = async () => {
      setLoading(true);
      setError(null);
      try {
        const result = await SERVICES.WORKFLOW.getWorkflowRunsList(workflowId, { perPage: 20 });
        if (cancelled) return;

        const rawRuns = result?.runs ?? [];

        if (selectedNodeId) {
          // Show per-node run entries extracted from each workflow run's nodeRuns
          const nodeEntries: RunEntry[] = rawRuns.flatMap(run => {
            const nodeRun = (run.nodeRuns ?? []).find(nr => nr.nodeId === selectedNodeId);
            if (!nodeRun) return [];
            const statusMap: Record<string, RunEntry['status']> = {
              running: 'live',
              pending: 'live',
              completed: 'ok',
              failed: 'fail',
              skipped: 'fail',
              cancelled: 'fail',
            };
            return [
              {
                id: run.id.slice(-8),
                status: statusMap[nodeRun.status] ?? 'fail',
                time: `${formatTime(run.startedAt)} · attempt ${nodeRun.attempt}`,
                dur: formatDuration(nodeRun.startedAt ?? run.startedAt, nodeRun.finishedAt),
                err: nodeRun.error ?? undefined,
                actions: nodeRun.status === 'failed' ? ['retry'] : [],
              } satisfies RunEntry,
            ];
          });
          setRuns(nodeEntries);
        } else {
          setRuns(rawRuns.map(workflowRunToEntry));
        }
      } catch (err: any) {
        if (!cancelled) {
          setError(err?.message || err?.detail?.message || 'Failed to load run history');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    fetchRuns();
    return () => {
      cancelled = true;
    };
  }, [workflowId, selectedNodeId]);

  const heading = selectedNodeLabel ? `${selectedNodeLabel} — run history` : 'Run history';

  return (
    <div className='px-4 py-3'>
      <div className='font-mono text-[10px] font-bold tracking-wide uppercase text-[var(--jarvis-subtle)] mb-1.5'>
        {heading}
      </div>

      {!workflowId ? (
        <p className='text-xs text-[var(--jarvis-subtle)] text-center py-6'>Save the workflow to view run history</p>
      ) : !selectedNodeId ? (
        <p className='text-xs text-[var(--jarvis-subtle)] text-center py-6'>Select a node to view its run history</p>
      ) : loading ? (
        <div className='flex justify-center py-6'>
          <div className='h-5 w-5 animate-spin rounded-full border-b-2 border-[var(--jarvis-primary)]' />
        </div>
      ) : error ? (
        <p className='text-xs text-[var(--jarvis-danger-text)] text-center py-6'>{error}</p>
      ) : runs.length === 0 ? (
        <p className='text-xs text-[var(--jarvis-subtle)] text-center py-6'>No runs yet</p>
      ) : (
        runs.map(r => <RunRow key={r.id} run={r} />)
      )}
    </div>
  );
};

export { RunHistory };
export default RunHistory;
