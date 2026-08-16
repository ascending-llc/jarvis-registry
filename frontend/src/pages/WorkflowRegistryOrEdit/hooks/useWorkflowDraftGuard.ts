import type { Edge } from '@xyflow/react';
import type { RefObject } from 'react';
import { useCallback, useLayoutEffect, useRef } from 'react';
import { getInitialElements } from '@/components/WorkflowCanvas/fixtures';
import type { WorkflowCanvasRef, WorkflowNode } from '@/components/WorkflowCanvas/types';
import type { Workflow } from '@/services/workflow/type';
import { createWorkflowDraftSnapshot, type WorkflowDraftMetadata } from '../workflowDraftSnapshot';

interface UseWorkflowDraftGuardOptions {
  canvasRef: RefObject<WorkflowCanvasRef | null>;
  workflow: Partial<Workflow> | null;
  resourceKey?: string;
  isReadOnly: boolean;
  initialNodes?: WorkflowNode[];
  initialEdges?: Edge[];
}

const NEW_WORKFLOW_KEY = '__new_workflow__';

const _matchesResource = (workflow: Partial<Workflow> | null, resourceKey?: string): boolean => {
  if (!workflow) return false;
  if (resourceKey) return workflow.id === resourceKey;
  return workflow.id === undefined;
};

const _getInitialElements = (
  resourceKey: string | undefined,
  initialNodes: WorkflowNode[] | undefined,
  initialEdges: Edge[] | undefined,
): { nodes: WorkflowNode[]; edges: Edge[] } => {
  if (!resourceKey && !initialNodes && !initialEdges) return getInitialElements();
  return { nodes: initialNodes ?? [], edges: initialEdges ?? [] };
};

/** Keeps a persisted-content baseline without relying on React Flow change events. */
export const useWorkflowDraftGuard = ({
  canvasRef,
  workflow,
  resourceKey,
  isReadOnly,
  initialNodes,
  initialEdges,
}: UseWorkflowDraftGuardOptions) => {
  const baselineRef = useRef<string | null>(null);
  const baselineKeyRef = useRef<string | null>(null);
  const baselineInitializedRef = useRef(false);
  const baselineOwnerRef = useRef<string | null>(null);
  const workflowRef = useRef(workflow);
  const baselineKey = resourceKey ?? NEW_WORKFLOW_KEY;
  const currentResourceKeyRef = useRef<string | null>(null);

  useLayoutEffect(() => {
    currentResourceKeyRef.current = baselineKey;
    workflowRef.current = workflow;

    if (baselineOwnerRef.current !== baselineKey) {
      baselineOwnerRef.current = baselineKey;
      baselineRef.current = null;
      baselineKeyRef.current = null;
      baselineInitializedRef.current = false;
    }

    if (isReadOnly || !_matchesResource(workflow, resourceKey) || baselineInitializedRef.current) return;

    const elements = _getInitialElements(resourceKey, initialNodes, initialEdges);
    baselineRef.current = createWorkflowDraftSnapshot(workflow, elements.nodes, elements.edges);
    baselineKeyRef.current = baselineKey;
    baselineInitializedRef.current = true;
  }, [baselineKey, initialEdges, initialNodes, isReadOnly, resourceKey, workflow]);

  const isDirty = useCallback((): boolean => {
    const currentWorkflow = workflowRef.current;
    if (isReadOnly || baselineKeyRef.current !== baselineKey || !_matchesResource(currentWorkflow, resourceKey))
      return false;

    const elements = canvasRef.current?.getElements();
    if (!elements || baselineRef.current === null) return false;

    return createWorkflowDraftSnapshot(currentWorkflow, elements.nodes, elements.edges) !== baselineRef.current;
  }, [baselineKey, canvasRef, isReadOnly, resourceKey]);

  const markSaved = useCallback(
    (
      metadata: WorkflowDraftMetadata,
      nodes: WorkflowNode[],
      edges: Edge[],
      submittedWorkflow: Partial<Workflow> | null,
    ) => {
      if (currentResourceKeyRef.current !== baselineKey || baselineOwnerRef.current !== baselineKey) return;
      if (workflowRef.current === submittedWorkflow && submittedWorkflow) {
        workflowRef.current = { ...submittedWorkflow, ...metadata };
      }
      baselineRef.current = createWorkflowDraftSnapshot(metadata, nodes, edges);
      baselineKeyRef.current = baselineKey;
      baselineInitializedRef.current = true;
    },
    [baselineKey],
  );

  const discardChanges = useCallback(() => {
    if (currentResourceKeyRef.current !== baselineKey || baselineOwnerRef.current !== baselineKey) return;
    baselineRef.current = null;
    baselineKeyRef.current = null;
    baselineInitializedRef.current = true;
  }, [baselineKey]);

  return { discardChanges, isDirty, markSaved };
};
