import type { Edge } from '@xyflow/react';
import type React from 'react';
import { createContext, useCallback, useContext, useMemo } from 'react';

import { useAuth } from '@/contexts/AuthContext';
import SERVICES from '@/services';
import type {
  ResolveRequirementRequest,
  StepRequirementSummary,
  WorkflowRunStatusResponse,
} from '@/services/workflow/type';
import type { WorkflowNode } from '../types';

interface WorkflowRuntimeContextValue {
  activeRun: WorkflowRunStatusResponse | null;
  canControlWorkflow: boolean;
  getPendingConfirmation: (gateNodeId: string) => StepRequirementSummary | null;
  resolveRequirement: (
    requirement: StepRequirementSummary,
    resolution: ResolveRequirementRequest['resolution'],
    feedback?: string,
  ) => Promise<void>;
  refetchActiveRun: () => Promise<void>;
}

interface WorkflowRuntimeProviderProps {
  workflowId?: string;
  activeRun: WorkflowRunStatusResponse | null;
  nodes: WorkflowNode[];
  edges: Edge[];
  refetchActiveRun: () => Promise<void>;
  children: React.ReactNode;
}

const WorkflowRuntimeContext = createContext<WorkflowRuntimeContextValue | null>(null);

export const useWorkflowRuntime = (): WorkflowRuntimeContextValue => {
  const context = useContext(WorkflowRuntimeContext);
  if (!context) throw new Error('useWorkflowRuntime must be used within WorkflowRuntimeProvider');
  return context;
};

export const WorkflowRuntimeProvider: React.FC<WorkflowRuntimeProviderProps> = ({
  workflowId,
  activeRun,
  nodes,
  edges,
  refetchActiveRun,
  children,
}) => {
  const { user } = useAuth();
  const canControlWorkflow = user?.scopes?.includes('workflows-control') === true;

  const requirementByGateId = useMemo(() => {
    const result = new Map<string, StepRequirementSummary>();
    if (activeRun?.status !== 'awaiting_approval') return result;

    const nodeById = new Map(nodes.map(node => [node.id, node]));

    for (const gate of nodes) {
      if (gate.type !== 'gate') continue;

      const targetNodeId = edges.find(edge => edge.source === gate.id)?.target;
      const targetName = targetNodeId ? nodeById.get(targetNodeId)?.data.label : undefined;
      if (!targetName) continue;

      const requirement = activeRun.pendingRequirements.find(
        item => item.stepName === targetName && item.requiresConfirmation === true && item.confirmed === null,
      );
      if (requirement) result.set(gate.id, requirement);
    }

    return result;
  }, [activeRun, edges, nodes]);

  const getPendingConfirmation = useCallback(
    (gateNodeId: string): StepRequirementSummary | null => requirementByGateId.get(gateNodeId) ?? null,
    [requirementByGateId],
  );

  const resolveRequirement = useCallback(
    async (
      requirement: StepRequirementSummary,
      resolution: ResolveRequirementRequest['resolution'],
      feedback?: string,
    ): Promise<void> => {
      if (!workflowId || !activeRun || !canControlWorkflow) {
        throw new Error('Workflow approval is not available');
      }

      await SERVICES.WORKFLOW.approveWorkflowRun(workflowId, activeRun.runId, {
        stepId: requirement.stepId,
        resolution,
        feedback,
      });
    },
    [activeRun, canControlWorkflow, workflowId],
  );

  const value = useMemo<WorkflowRuntimeContextValue>(
    () => ({
      activeRun,
      canControlWorkflow,
      getPendingConfirmation,
      resolveRequirement,
      refetchActiveRun,
    }),
    [activeRun, canControlWorkflow, getPendingConfirmation, refetchActiveRun, resolveRequirement],
  );

  return <WorkflowRuntimeContext.Provider value={value}>{children}</WorkflowRuntimeContext.Provider>;
};
