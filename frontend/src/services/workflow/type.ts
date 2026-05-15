export type WorkflowPermissionType = {
  VIEW?: boolean;
  EDIT?: boolean;
  DELETE?: boolean;
};

export type WorkflowItem = {
  id: string;
  name: string;
  description: string;
  type: 'autonomous' | 'supervised';
  nodeCount: number;
  enabled: boolean;
  status: 'active' | 'inactive' | 'error';
  lastRunAt?: string;
  runCount: number;
  permissions: WorkflowPermissionType;
  createdAt: string;
  updatedAt: string;
};

export interface Pagination {
  total: number;
  page: number;
  perPage: number;
  totalPages: number;
}

export interface StepConfig {
  maxRetries?: number;
  onError?: 'fail' | 'skip' | 'retry';
  backoffBaseSeconds?: number;
  backoffMaxSeconds?: number;
}

export interface LoopConfig {
  maxIterations: number;
  endConditionCel?: string;
}

export interface WorkflowNode {
  id?: string;
  name: string;
  nodeType: 'step' | 'parallel' | 'loop' | 'condition' | 'router';
  executorKey?: string | null;
  a2aPool?: string[];
  stepConfig?: StepConfig | null;
  config: Record<string, any>;
  children?: WorkflowNode[];
  conditionCel?: string | null;
  loopConfig?: LoopConfig | null;
}

export interface Workflow {
  id: string;
  name: string;
  description?: string;
  numNodes?: number;
  nodes?: WorkflowNode[];
  createdAt: string;
  updatedAt: string;
}

export interface GetWorkflowsListRequest {
  query?: string;
  page?: number;
  perPage?: number;
}

export interface GetWorkflowsListResponse {
  workflows: Workflow[];
  pagination: Pagination;
}

export type GetWorkflowDetailResponse = Workflow;

export interface CreateWorkflowRequest {
  name: string;
  description?: string;
  nodes: WorkflowNode[];
}

export type CreateWorkflowResponse = Workflow;

export interface UpdateWorkflowRequest {
  name?: string;
  description?: string;
  nodes?: WorkflowNode[];
}

export interface ToggleWorkflowStateRequest {
  enabled: boolean;
}

export type ToggleWorkflowStateResponse = Workflow;

export type UpdateWorkflowResponse = Workflow;

export interface ResolvedDependency {
  nodeId: string;
  resolution: 'reuse_previous_output' | 'rerun';
  sourceNodeRunId?: string;
}

export interface TriggerWorkflowRunRequest {
  triggerSource?: string;
  initialInput?: Record<string, any>;
  parentRunId?: string;
  resolvedDependencies?: ResolvedDependency[];
}

export interface TriggerWorkflowRunResponse {
  runId: string;
  workflowDefinitionId: string;
  status: string;
  triggerSource: string;
  startedAt: string;
  message: string;
}

export interface GetWorkflowRunsListRequest {
  status?: 'pending' | 'running' | 'paused' | 'completed' | 'failed' | 'cancelled';
  page?: number;
  perPage?: number;
}

export interface NodeRun {
  id: string;
  workflowRunId: string;
  nodeId: string;
  nodeName: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped' | 'cancelled';
  attempt: number;
  inputSnapshot?: Record<string, any> | null;
  outputSnapshot?: Record<string, any> | null;
  error?: string | null;
  startedAt?: string;
  finishedAt?: string;
}

export interface WorkflowRun {
  id: string;
  workflowDefinitionId: string;
  status: 'pending' | 'running' | 'paused' | 'completed' | 'failed' | 'cancelled';
  triggerSource?: string;
  startedAt: string;
  finishedAt?: string;
  parentRunId?: string | null;
  errorSummary?: string | null;
  nodeRuns?: NodeRun[];
  initialInput?: Record<string, any>;
  finalOutput?: Record<string, any>;
  definitionSnapshot?: Omit<Workflow, 'id' | 'createdAt' | 'updatedAt'>;
  resolvedDependencies?: ResolvedDependency[];
}

export interface GetWorkflowRunsListResponse {
  runs: WorkflowRun[];
  pagination: Pagination;
}

export type GetWorkflowRunDetailResponse = WorkflowRun;
