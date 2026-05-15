import type React from 'react';
import { useEffect, useState } from 'react';
import SERVICES from '@/services';
import type { NodeRun, WorkflowRun } from '@/services/workflow/type';
import type { RunEntry } from '../types';

// ─── helpers ──────────────────────────────────────────────────────────────────

const STATUS_MAP: Record<string, RunEntry['status']> = {
  running: 'live',
  pending: 'live',
  paused: 'paused',
  completed: 'ok',
  failed: 'fail',
  cancelled: 'fail',
};

const ACTIONS_BY_STATUS: Record<string, RunEntry['actions']> = {
  running: ['pause', 'cancel'],
  pending: ['cancel'],
  paused: ['resume', 'cancel'],
  completed: [],
  failed: ['retry'],
  cancelled: ['retry'],
};

const NODE_STATUS_MAP: Record<string, RunEntry['status']> = {
  running: 'live',
  pending: 'live',
  completed: 'ok',
  failed: 'fail',
  skipped: 'fail',
  cancelled: 'fail',
};

/** Normalize list/detail payloads (camelCase or snake_case). */
const normalizeWorkflowRun = (raw: Record<string, unknown>): WorkflowRun => {
  const nodeRunsRaw = (raw.nodeRuns ?? raw.node_runs) as Record<string, unknown>[] | undefined;
  const nodeRuns: NodeRun[] | undefined = nodeRunsRaw?.map(nr => ({
    id: String(nr.id ?? ''),
    workflowRunId: String(nr.workflowRunId ?? nr.workflow_run_id ?? ''),
    nodeId: String(nr.nodeId ?? nr.node_id ?? ''),
    nodeName: String(nr.nodeName ?? nr.node_name ?? ''),
    status: (nr.status ?? 'pending') as NodeRun['status'],
    attempt: Number(nr.attempt ?? 0),
    inputSnapshot: (nr.inputSnapshot ?? nr.input_snapshot) as NodeRun['inputSnapshot'],
    outputSnapshot: (nr.outputSnapshot ?? nr.output_snapshot) as NodeRun['outputSnapshot'],
    error: (nr.error as string | null) ?? null,
    startedAt: (nr.startedAt ?? nr.started_at) as string | undefined,
    finishedAt: (nr.finishedAt ?? nr.finished_at) as string | undefined,
  }));

  return {
    id: String(raw.id ?? ''),
    workflowDefinitionId: String(raw.workflowDefinitionId ?? raw.workflow_definition_id ?? ''),
    status: (raw.status ?? 'pending') as WorkflowRun['status'],
    triggerSource: (raw.triggerSource ?? raw.trigger_source) as string | undefined,
    startedAt: String(raw.startedAt ?? raw.started_at ?? ''),
    finishedAt: (raw.finishedAt ?? raw.finished_at) as string | undefined,
    parentRunId: (raw.parentRunId ?? raw.parent_run_id) as string | null | undefined,
    errorSummary: (raw.errorSummary ?? raw.error_summary) as string | null | undefined,
    nodeRuns,
  };
};

const normalizeRunsList = (result: unknown): WorkflowRun[] => {
  if (!result || typeof result !== 'object') return [];
  const obj = result as Record<string, unknown>;
  const list = obj.runs ?? obj.data;
  if (!Array.isArray(list)) return [];
  return list.map(item => normalizeWorkflowRun(item as Record<string, unknown>));
};

const formatDuration = (startedAt: string, finishedAt?: string): string | undefined => {
  if (!finishedAt || !startedAt) return undefined;
  const ms = new Date(finishedAt).getTime() - new Date(startedAt).getTime();
  if (ms < 0) return undefined;
  const secs = Math.floor(ms / 1000);
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
};

const formatTime = (iso: string): string => {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const now = new Date();
  const hhmm = d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  if (d.toDateString() === now.toDateString()) return `Today ${hhmm}`;
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (d.toDateString() === yesterday.toDateString()) return `Yesterday ${hhmm}`;
  return `${d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })} ${hhmm}`;
};

const workflowRunToEntry = (run: WorkflowRun): RunEntry => ({
  id: run.id.slice(-8),
  status: STATUS_MAP[run.status] ?? 'fail',
  time: formatTime(run.startedAt),
  dur: formatDuration(run.startedAt, run.finishedAt),
  err: run.errorSummary ?? undefined,
  actions: ACTIONS_BY_STATUS[run.status] ?? [],
});

const nodeRunToEntry = (run: WorkflowRun, nodeRun: NodeRun): RunEntry => ({
  id: run.id.slice(-8),
  status: NODE_STATUS_MAP[nodeRun.status] ?? 'fail',
  time: `${formatTime(run.startedAt)} · attempt ${nodeRun.attempt}`,
  dur: formatDuration(nodeRun.startedAt ?? run.startedAt, nodeRun.finishedAt),
  err: nodeRun.error ?? run.errorSummary ?? undefined,
  actions: nodeRun.status === 'failed' ? ['retry'] : [],
});

const matchesSelectedNode = (
  nodeRun: NodeRun,
  selectedNodeId: string,
  selectedNodeLabel?: string,
): boolean => {
  if (nodeRun.nodeId === selectedNodeId) return true;
  if (selectedNodeLabel && nodeRun.nodeName === selectedNodeLabel) return true;
  return false;
};

/** List endpoint may omit nodeRuns — fetch detail for runs missing them (cap for perf). */
const enrichRunsWithNodeRuns = async (workflowId: string, runs: WorkflowRun[]): Promise<WorkflowRun[]> => {
  const needsDetail = runs.filter(r => !r.nodeRuns?.length).slice(0, 10);
  if (needsDetail.length === 0) return runs;

  const detailById = new Map<string, WorkflowRun>();
  await Promise.all(
    needsDetail.map(async run => {
      try {
        const detail = await SERVICES.WORKFLOW.getWorkflowRunDetail(workflowId, run.id);
        detailById.set(run.id, normalizeWorkflowRun(detail as unknown as Record<string, unknown>));
      } catch {
        detailById.set(run.id, run);
      }
    }),
  );

  return runs.map(r => detailById.get(r.id) ?? r);
};

const buildNodeRunEntries = (
  runs: WorkflowRun[],
  selectedNodeId: string,
  selectedNodeLabel?: string,
): RunEntry[] =>
  runs.flatMap(run => {
    const nodeRun = (run.nodeRuns ?? []).find(nr => matchesSelectedNode(nr, selectedNodeId, selectedNodeLabel));
    return nodeRun ? [nodeRunToEntry(run, nodeRun)] : [];
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


interface RunHistoryProps {
  workflowId?: string;
  refreshRunHistoryKey?: number;
  selectedNodeId?: string;
  selectedNodeLabel?: string;
}

const RunHistory: React.FC<RunHistoryProps> = ({
  workflowId,
  refreshRunHistoryKey = 0,
  selectedNodeId,
  selectedNodeLabel,
}) => {
  const [runs, setRuns] = useState<RunEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAllWorkflowRuns, setShowAllWorkflowRuns] = useState(false);

  useEffect(() => {
    if (!workflowId) {
      setRuns([]);
      return;
    }

    let cancelled = false;

    const fetchRuns = async () => {
      setLoading(true);
      setError(null);
      setShowAllWorkflowRuns(false);
      try {
        const result = await SERVICES.WORKFLOW.getWorkflowRunsList(workflowId, { perPage: 20 });
        if (cancelled) return;

        const rawRuns = normalizeRunsList(result);
        const enriched = await enrichRunsWithNodeRuns(workflowId, rawRuns);

        let entries: RunEntry[];
        if (selectedNodeId) {
          entries = buildNodeRunEntries(enriched, selectedNodeId, selectedNodeLabel);
          if (entries.length === 0 && enriched.length > 0) {
            entries = enriched.map(workflowRunToEntry);
            setShowAllWorkflowRuns(true);
          }
        } else {
          entries = enriched.map(workflowRunToEntry);
        }

        setRuns(entries);
      } catch (err: unknown) {
        if (!cancelled) {
          const e = err as { message?: string; detail?: { message?: string } | string };
          const msg =
            e?.message ||
            (typeof e?.detail === 'object' ? e.detail?.message : undefined) ||
            (typeof e?.detail === 'string' ? e.detail : undefined) ||
            'Failed to load run history';
          setError(msg);
          setRuns([]);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    fetchRuns();
    return () => {
      cancelled = true;
    };
  }, [workflowId, selectedNodeId, selectedNodeLabel, refreshRunHistoryKey]);

  const heading = selectedNodeLabel ? `${selectedNodeLabel} — run history` : 'Run history';

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
      ) : runs.length === 0 ? (
        <p className='text-xs text-[var(--jarvis-subtle)] text-center py-6'>
          {selectedNodeId ? 'No runs for this node yet' : 'No runs yet'}
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
