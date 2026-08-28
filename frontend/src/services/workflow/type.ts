export type WorkflowPermissionType = {
  VIEW: boolean;
  EDIT: boolean;
  DELETE: boolean;
  SHARE: boolean;
};

export const EMPTY_WORKFLOW_PERMISSIONS: WorkflowPermissionType = {
  VIEW: false,
  EDIT: false,
  DELETE: false,
  SHARE: false,
};

export type WorkflowItem = {
  id: string;
  name: string;
  description: string;
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

export interface RouterChoice {
  name: string;
  steps: WorkflowNode[];
}

export interface HumanReviewConfig {
  requiresConfirmation?: boolean;
  confirmationMessage?: string;
  requiresUserInput?: boolean;
  userInputMessage?: string;
  userInputSchema?: any[];
  requiresOutputReview?: boolean;
  outputReviewMessage?: string;
  requiresIterationReview?: boolean;
  iterationReviewMessage?: string;
  onReject?: 'skip' | 'else_branch' | 'fail';
  timeoutSeconds?: number;
  onTimeout?: 'cancel' | 'skip' | 'approve';
}

export interface WorkflowNode {
  id?: string;
  name: string;
  nodeType: 'step' | 'parallel' | 'loop' | 'condition' | 'router';
  executorKey?: string | null;
  stepObjective?: string | null;
  referencedNodeNames?: string[] | null;
  a2aPool?: string[];
  stepConfig?: StepConfig | null;
  config: Record<string, any>;
  children?: WorkflowNode[];
  trueSteps?: WorkflowNode[];
  falseSteps?: WorkflowNode[];
  choices?: RouterChoice[];
  conditionCel?: string | null;
  loopConfig?: LoopConfig | null;
  humanReview?: HumanReviewConfig | null;
  position?: { x?: number; y?: number };
}

export interface Workflow {
  id: string;
  name: string;
  description?: string;
  numNodes?: number;
  nodes?: WorkflowNode[];
  canvas?: { viewport: { x?: number; y?: number; zoom?: number } };
  createdAt: string;
  updatedAt: string;
  aclPermission?: WorkflowPermissionType | null;
  permissions: WorkflowPermissionType;
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
  canvas: { viewport: { x?: number; y?: number; zoom?: number } };
}

export type CreateWorkflowResponse = Workflow;

export interface UpdateWorkflowRequest {
  name?: string;
  description?: string;
  nodes?: WorkflowNode[];
  canvas?: { viewport: { x?: number; y?: number; zoom?: number } };
}

export interface ToggleWorkflowStateRequest {
  enabled: boolean;
}

export type ToggleWorkflowStateResponse = Workflow;

export type UpdateWorkflowResponse = Workflow;

export const WORKFLOW_RUN_STATUSES = [
  'pending',
  'running',
  'paused',
  'awaiting_approval',
  'completed',
  'failed',
  'cancelled',
] as const;

export type WorkflowRunStatus = (typeof WORKFLOW_RUN_STATUSES)[number];

export const TERMINAL_RUN_STATUSES: ReadonlySet<WorkflowRunStatus> = new Set(['completed', 'failed', 'cancelled']);

export const isWorkflowRunStatus = (value: unknown): value is WorkflowRunStatus =>
  typeof value === 'string' && (WORKFLOW_RUN_STATUSES as readonly string[]).includes(value);

export interface StepRequirementSummary {
  stepId: string;
  stepName?: string;
  requiresConfirmation: boolean;
  confirmationMessage?: string;
  confirmed: boolean | null;
  onReject: 'skip' | 'cancel' | 'retry' | 'else_branch';
}

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

export interface PendingAuthorization {
  serverId: string;
  serverName: string;
  authUrl: string;
  flowId: string;
}

export type TriggerWorkflowRunResponse =
  | {
      requiresReauth: false;
      runId: string;
      workflowDefinitionId: string;
      status: WorkflowRunStatus;
      triggerSource: string;
      startedAt: string;
      message: string;
    }
  | {
      requiresReauth: true;
      pendingAuthorizations: PendingAuthorization[];
      message: string;
    };

export interface GetWorkflowRunsListRequest {
  status?: WorkflowRunStatus;
  page?: number;
  perPage?: number;
}

export const NODE_RUN_STATUSES = [
  'pending',
  'running',
  'awaiting_approval',
  'completed',
  'failed',
  'skipped',
  'cancelled',
] as const;

export type NodeRunStatus = (typeof NODE_RUN_STATUSES)[number];

export interface NodeRun {
  id: string;
  workflowRunId: string;
  nodeId: string;
  nodeName: string;
  status: NodeRunStatus;
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
  status: WorkflowRunStatus;
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
  pendingRequirements?: StepRequirementSummary[];
}

export interface GetWorkflowRunsListResponse {
  runs: WorkflowRun[];
  pagination: Pagination;
}

export type GetWorkflowRunDetailResponse = WorkflowRun;

export interface NodeRunSummary {
  nodeId: string;
  nodeName: string;
  status: NodeRunStatus;
  attempt: number;
  startedAt?: string | null;
  finishedAt?: string | null;
  error?: string | null;
}

export interface WorkflowRunStatusResponse {
  runId: string;
  workflowId: string;
  status: WorkflowRunStatus;
  pendingRequirements: StepRequirementSummary[];
  nodeRuns: NodeRunSummary[];
}

export interface ResolveRequirementRequest {
  stepId: string;
  resolution: 'confirm' | 'reject';
  feedback?: string;
}

export interface ResolveRequirementResponse {
  runId: string;
  status: WorkflowRunStatus;
  resolvedStepId: string;
  message: string;
}

export interface ReplayWorkflowRunResponse {
  runId: string;
  status: WorkflowRunStatus;
  message: string;
}

export interface RerunWorkflowNodeResponse {
  runId: string;
  status: WorkflowRunStatus;
  message: string;
}
