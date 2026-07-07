import type { Edge, Node } from '@xyflow/react';
import type { Workflow } from '@/services/workflow/type';

export type PanelMode = 'node' | 'workflow';

export interface WorkflowCanvasRef {
  save: () => void;
  getElements: () => { nodes: Node[]; edges: Edge[] };
  clearSelection: () => void;
  /** Toggle panel: expand if collapsed, collapse if expanded and workflow mode */
  togglePanel: () => void;
}

/** WorkflowCanvas main component Props */
export interface WorkflowCanvasProps {
  workflowId?: string;
  workflow?: Partial<Workflow> | null;
  refreshRunHistoryKey?: number;
  initialNodes?: Node[];
  initialEdges?: Edge[];
  isReadOnly: boolean;
  isNewWorkflow: boolean;
  onDeleteWorkflow: () => void;
  onWorkflowChange: (patch: Partial<Pick<Workflow, 'name' | 'description'>>) => void;
  onSave?: (nodes: Node[], edges: Edge[], viewport: { x: number; y: number; zoom: number }) => void;
  onChange?: () => void;
}

/** Base node data */
export interface BaseNodeData extends Record<string, unknown> {
  label: string;
  description?: string;
  executorKey?: string;
  onAdd?: () => void;
}

/** Specific node data types */
export interface GateNodeData extends BaseNodeData {
  reviewerPrompt?: string;
  timeout?: string;
  onTimeout?: 'cancel' | 'skip' | 'approve';
}

export interface CondNodeData extends BaseNodeData {
  expression?: string;
}

export interface RouterNodeData extends BaseNodeData {
  routeBy?: string;
  cases?: string[];
  defaultCase?: string;
}

export interface LoopNodeData extends BaseNodeData {
  agents?: AgentInfo[];
  maxIterations?: number;
  exitCondition?: string;
}

export interface ParallelNodeData extends BaseNodeData {
  branches?: string[];
}

export interface PoolNodeData extends BaseNodeData {
  agents?: AgentInfo[];
}

export interface AgentNodeData extends BaseNodeData {
  executorKey: string;
}
export interface McpNodeData extends BaseNodeData {
  executorKey: string;
}

/** Union type of workflow node data */
export type NodeData =
  | GateNodeData
  | CondNodeData
  | RouterNodeData
  | LoopNodeData
  | ParallelNodeData
  | PoolNodeData
  | AgentNodeData
  | McpNodeData;

/** Workflow node type for ReactFlow. */
export type WorkflowNode = Node<NodeData>;

/** PropsPanel Props */
export interface PropsPanelProps {
  panelMode: PanelMode;
  isReadOnly: boolean;
  isNewWorkflow: boolean;
  collapsed: boolean;
  onCollapsedChange: (collapsed: boolean | ((prev: boolean) => boolean)) => void;
}

/** NodePicker Props */
export interface NodePickerProps {
  onPick: (type: 'agent' | 'mcp' | 'logic', item: PickerItem | LogicStep) => void;
  onClose: () => void;
  agentOnly?: boolean;
  tab?: string;
  onTabChange?: (tab: string) => void;
}

/** Schema field type (used for CEL Context Reference) */
export interface SchemaField {
  name: string;
  desc: string;
  type: string;
  enum?: string[];
}

/** Picker selected item type */
export interface PickerItem {
  id: string;
  label: string;
  desc: string;
  enabled?: boolean;
  executorKey: string;
}

/** Agent info type */
export interface AgentInfo {
  id: string;
  label: string;
  desc: string;
  path: string;
}

/** Logic Step type */
export interface LogicStep {
  id: string;
  label: string;
  desc: string;
  icon: string;
  color: string;
  accent: string;
  iconStyle?: React.CSSProperties;
}

/** Run history entry */
export interface RunEntry {
  id: string;
  fullId: string;
  type: 'workflow' | 'node';
  status: 'ok' | 'fail' | 'live' | 'paused';
  time: string;
  dur?: string;
  err?: string;
  actions?: ('pause' | 'cancel' | 'resume' | 'retry')[];
  input?: Record<string, any>;
  output?: Record<string, any>;
  nodeName?: string;
  nodeId?: string;
  nodeType?: string;
}
