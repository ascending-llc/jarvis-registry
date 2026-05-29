import { useEffect, useState } from 'react';
import SERVICES from '@/services';
import type { PanelMode, RunEntry } from '../../types';
import {
  buildNodeRunEntries,
  enrichRunsWithNodeRuns,
  normalizeRunsList,
  workflowRunToEntry,
} from '../utils/runHistoryUtils';
import { useWorkflowPanel } from '../WorkflowPanelContext';

interface UseRunHistoryProps {
  panelMode: PanelMode;
}

export const useRunHistory = ({ panelMode }: UseRunHistoryProps) => {
  const { workflowId, selectedNode, refreshRunHistoryKey = 0 } = useWorkflowPanel();
  const selectedNodeId = selectedNode?.id;
  const selectedNodeLabel = selectedNode?.data?.label as string | undefined;

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
        if (panelMode === 'workflow') {
          entries = enriched.map(workflowRunToEntry);
        } else if (selectedNodeId) {
          entries = buildNodeRunEntries(enriched, selectedNodeId, selectedNodeLabel);
          if (entries.length === 0 && enriched.length > 0) {
            entries = enriched.map(workflowRunToEntry);
            setShowAllWorkflowRuns(true);
          }
        } else {
          entries = [];
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
  }, [workflowId, panelMode, selectedNodeId, selectedNodeLabel, refreshRunHistoryKey]);

  return { runs, loading, error, showAllWorkflowRuns, workflowId, selectedNodeId, selectedNodeLabel };
};
