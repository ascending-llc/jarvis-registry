import SERVICES from '@/services';
import type { NodeRun, WorkflowRun } from '@/services/workflow/type';
import type { RunEntry } from '../../types';

export const STATUS_MAP: Record<string, RunEntry['status']> = {
  running: 'live',
  pending: 'live',
  paused: 'paused',
  completed: 'ok',
  failed: 'fail',
  cancelled: 'fail',
};

export const ACTIONS_BY_STATUS: Record<string, RunEntry['actions']> = {
  running: ['pause', 'cancel'],
  pending: ['cancel'],
  paused: ['resume', 'cancel'],
  completed: [],
  failed: ['retry'],
  cancelled: ['retry'],
};

export const NODE_STATUS_MAP: Record<string, RunEntry['status']> = {
  running: 'live',
  pending: 'live',
  completed: 'ok',
  failed: 'fail',
  skipped: 'fail',
  cancelled: 'fail',
};

export const normalizeWorkflowRun = (raw: Record<string, unknown>): WorkflowRun => {
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

export const normalizeRunsList = (result: unknown): WorkflowRun[] => {
  if (!result || typeof result !== 'object') return [];
  const obj = result as Record<string, unknown>;
  const list = obj.runs ?? obj.data;
  if (!Array.isArray(list)) return [];
  return list.map(item => normalizeWorkflowRun(item as Record<string, unknown>));
};

export const formatDuration = (startedAt: string, finishedAt?: string): string | undefined => {
  if (!finishedAt || !startedAt) return undefined;
  const ms = new Date(finishedAt).getTime() - new Date(startedAt).getTime();
  if (ms < 0) return undefined;
  const secs = Math.floor(ms / 1000);
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
};

export const formatTime = (iso: string): string => {
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

export const workflowRunToEntry = (run: WorkflowRun): RunEntry => ({
  id: run.id.slice(-8),
  status: STATUS_MAP[run.status] ?? 'fail',
  time: formatTime(run.startedAt),
  dur: formatDuration(run.startedAt, run.finishedAt),
  err: run.errorSummary ?? undefined,
  actions: ACTIONS_BY_STATUS[run.status] ?? [],
});

export const nodeRunToEntry = (run: WorkflowRun, nodeRun: NodeRun): RunEntry => ({
  id: run.id.slice(-8),
  status: NODE_STATUS_MAP[nodeRun.status] ?? 'fail',
  time: `${formatTime(run.startedAt)} · attempt ${nodeRun.attempt}`,
  dur: formatDuration(nodeRun.startedAt ?? run.startedAt, nodeRun.finishedAt),
  err: nodeRun.error ?? run.errorSummary ?? undefined,
  actions: nodeRun.status === 'failed' ? ['retry'] : [],
});

export const matchesSelectedNode = (nodeRun: NodeRun, selectedNodeId: string, selectedNodeLabel?: string): boolean => {
  if (nodeRun.nodeId === selectedNodeId) return true;
  if (selectedNodeLabel && nodeRun.nodeName === selectedNodeLabel) return true;
  return false;
};

export const enrichRunsWithNodeRuns = async (workflowId: string, runs: WorkflowRun[]): Promise<WorkflowRun[]> => {
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

export const buildNodeRunEntries = (
  runs: WorkflowRun[],
  selectedNodeId: string,
  selectedNodeLabel?: string,
): RunEntry[] =>
  runs.flatMap(run => {
    const nodeRun = (run.nodeRuns ?? []).find(nr => matchesSelectedNode(nr, selectedNodeId, selectedNodeLabel));
    return nodeRun ? [nodeRunToEntry(run, nodeRun)] : [];
  });
