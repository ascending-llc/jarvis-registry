import type { Edge, Node } from '@xyflow/react';
import type React from 'react';
import { createContext, useContext } from 'react';
import type { Workflow } from '@/services/workflow/type';
import type { AgentInfo, NodeData, SchemaField } from '../types';

export interface WorkflowPanelContextValue {
  workflowId?: string;
  refreshRunHistoryKey?: number;
  workflow: Partial<Workflow> | null;
  selectedNode: Node | null;
  nodes: Node[];
  edges: Edge[];
  agentSchemas: Record<string, { output: SchemaField[] }>;
  onOpenAgentPicker: (callback: (agent: AgentInfo) => void) => void;
  onNodeDataChange: (nodeId: string, patch: Partial<NodeData>) => void;
  onParallelBranchesChange: (nodeId: string, prev: string[], next: string[]) => void;
  onRouterCasesChange: (nodeId: string, prev: string[], next: string[]) => void;
  onDeleteNode: (nodeId: string) => void;
  onDeleteWorkflow: () => void;
  onWorkflowChange: (patch: Partial<Pick<Workflow, 'name' | 'description'>>) => void;
}

const WorkflowPanelContext = createContext<WorkflowPanelContextValue | null>(null);

export const useWorkflowPanel = () => {
  const ctx = useContext(WorkflowPanelContext);
  if (!ctx) {
    throw new Error('useWorkflowPanel must be used within WorkflowPanelProvider');
  }
  return ctx;
};

export const WorkflowPanelProvider: React.FC<WorkflowPanelContextValue & { children: React.ReactNode }> = ({
  children,
  ...value
}) => {
  return <WorkflowPanelContext.Provider value={value}>{children}</WorkflowPanelContext.Provider>;
};
