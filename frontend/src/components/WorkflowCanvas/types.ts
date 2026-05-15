import type { Edge, Node } from '@xyflow/react';

export interface WorkflowCanvasRef {
  save: () => void;
  getElements: () => { nodes: Node[]; edges: Edge[] };
}

/** WorkflowCanvas 主组件 Props */
export interface WorkflowCanvasProps {
  workflowId?: string;
  initialNodes?: Node[];
  initialEdges?: Edge[];
  onSave?: (nodes: Node[], edges: Edge[]) => void;
}

/** 节点数据类型 */
export interface NodeData extends Record<string, unknown> {
  label: string;
  description?: string;
  branches?: string[];
  cases?: string[];
  expression?: string;
  exitCondition?: string;
  maxIterations?: number;
  routeBy?: string;
  timeout?: string;
  defaultCase?: string;
  agents?: AgentInfo[];
  onAdd?: () => void;
}

/** Workflow node type for ReactFlow. */
export type WorkflowNode = Node<NodeData>;

/** PropsPanel Props */
export interface PropsPanelProps {
  workflowId?: string;
  selectedNode: Node | null;
  nodes: Node[];
  edges: Edge[];
  agentSchemas: Record<string, { output: SchemaField[] }>;
  onOpenAgentPicker: (callback: (agent: AgentInfo) => void) => void;
  onNodeDataChange: (nodeId: string, patch: Partial<NodeData>) => void;
  onParallelBranchesChange: (nodeId: string, prev: string[], next: string[]) => void;
  onDeleteNode: (nodeId: string) => void;
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

/** Schema 字段类型（用于 CEL Context Reference） */
export interface SchemaField {
  name: string;
  desc: string;
  type: string;
  enum?: string[];
}

/** Picker 选中项类型 */
export interface PickerItem {
  id: string;
  label: string;
  desc: string;
  status?: 'active' | 'inactive' | 'error';
}

/** Agent 信息类型 */
export interface AgentInfo {
  id: string;
  label: string;
  desc: string;
}

/** Logic Step 类型 */
export interface LogicStep {
  id: string;
  label: string;
  desc: string;
  icon: string;
  color: string;
  accent: string;
  iconStyle?: React.CSSProperties;
}

/** LogicProps Props（Gate/Cond/Router/Loop 共享） */
export interface LogicPropsProps {
  node: Node<NodeData>;
  nodes: Node[];
  edges: Edge[];
  upstreamSchema: SchemaField[] | null;
  sourceLabel: string | null;
  onNodeDataChange: (nodeId: string, patch: Partial<NodeData>) => void;
}

/** ParallelProps Props */
export interface ParallelPropsProps {
  node: Node<NodeData>;
  onNodeDataChange: (nodeId: string, patch: Partial<NodeData>) => void;
  onParallelBranchesChange: (nodeId: string, prev: string[], next: string[]) => void;
}

/** PoolProps Props */
export interface PoolPropsProps {
  node: Node<NodeData>;
  onNodeDataChange: (nodeId: string, patch: Partial<NodeData>) => void;
  onOpenAgentPicker: (callback: (agent: AgentInfo) => void) => void;
}

/** Run history entry */
export interface RunEntry {
  id: string;
  status: 'ok' | 'fail' | 'live' | 'paused';
  time: string;
  dur?: string;
  err?: string;
  actions?: ('pause' | 'cancel' | 'resume' | 'retry')[];
}
