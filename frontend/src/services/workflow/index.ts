import API from '@/services/api';
import Request from '@/services/request';

import {
  normalizeWorkflowRunStatusResponse,
  normalizeWorkflowSchedule,
  normalizeWorkflowSchedulesResponse,
} from './normalizers';
import type * as TYPE from './type';
import { EMPTY_WORKFLOW_PERMISSIONS } from './type';

const getWorkflowsList = async (data?: TYPE.GetWorkflowsListRequest): Promise<TYPE.GetWorkflowsListResponse> => {
  const response = await Request.get(API.getWorkflowsList, data);
  return {
    ...response,
    workflows: (response?.workflows ?? []).map((w: TYPE.Workflow) => ({
      ...w,
      permissions: w.aclPermission ?? EMPTY_WORKFLOW_PERMISSIONS,
    })),
  };
};

const getWorkflowDetail = async (id: string): Promise<TYPE.GetWorkflowDetailResponse> => {
  const data = await Request.get(API.getWorkflowDetail(id));
  return { ...data, permissions: data?.aclPermission ?? EMPTY_WORKFLOW_PERMISSIONS };
};

const createWorkflow = async (data: TYPE.CreateWorkflowRequest): Promise<TYPE.CreateWorkflowResponse> =>
  await Request.post(API.createWorkflow, data);

const updateWorkflow = async (id: string, data: TYPE.UpdateWorkflowRequest): Promise<TYPE.UpdateWorkflowResponse> => {
  const response = await Request.put(API.updateWorkflow(id), data);
  return { ...response, permissions: response?.aclPermission ?? EMPTY_WORKFLOW_PERMISSIONS };
};

const deleteWorkflow = async (id: string): Promise<void> => await Request.delete(API.deleteWorkflow(id));

const toggleWorkflowState = async (
  id: string,
  data: TYPE.ToggleWorkflowStateRequest,
): Promise<TYPE.ToggleWorkflowStateResponse> => await Request.post(API.toggleWorkflowState(id), data);

const triggerWorkflowRun = async (
  id: string,
  data: TYPE.TriggerWorkflowRunRequest,
): Promise<TYPE.TriggerWorkflowRunResponse> => await Request.post(API.triggerWorkflowRun(id), data);

const getWorkflowRunsList = async (
  id: string,
  data?: TYPE.GetWorkflowRunsListRequest,
): Promise<TYPE.GetWorkflowRunsListResponse> =>
  await Request.get(API.getWorkflowRunsList(id), {
    status: data?.status,
    page: data?.page,
    per_page: data?.perPage,
  });

const getWorkflowRunDetail = async (id: string, runId: string): Promise<TYPE.GetWorkflowRunDetailResponse> =>
  await Request.get(API.getWorkflowRunDetail(id, runId));

const getWorkflowRunStatus = async (id: string, runId: string): Promise<TYPE.WorkflowRunStatusResponse> => {
  const response = await Request.get(API.getWorkflowRunStatus(id, runId));
  return normalizeWorkflowRunStatusResponse(response);
};

const approveWorkflowRun = async (
  id: string,
  runId: string,
  data: TYPE.ResolveRequirementRequest,
): Promise<TYPE.ResolveRequirementResponse> => await Request.post(API.approveWorkflowRun(id, runId), data);

const replayWorkflowRun = async (id: string, runId: string): Promise<TYPE.ReplayWorkflowRunResponse> =>
  await Request.post(API.replayWorkflowRun(id, runId));

const rerunWorkflowNode = async (id: string, runId: string, nodeId: string): Promise<TYPE.RerunWorkflowNodeResponse> =>
  await Request.post(API.rerunWorkflowNode(id, runId, nodeId));

const getWorkflowSchedules = async (id: string): Promise<TYPE.GetWorkflowSchedulesResponse> => {
  const response = await Request.get(API.getWorkflowSchedules(id));
  return normalizeWorkflowSchedulesResponse(response);
};

const createWorkflowSchedule = async (
  id: string,
  data: TYPE.CreateWorkflowScheduleRequest,
): Promise<TYPE.WorkflowSchedule> => {
  const response = await Request.post(API.createWorkflowSchedule(id), {
    cron_expression: data.cronExpression,
    timezone: data.timezone,
    initial_input: data.initialInput ?? null,
  });
  return normalizeWorkflowSchedule(response);
};

const updateWorkflowSchedule = async (
  id: string,
  scheduleId: string,
  data: TYPE.UpdateWorkflowScheduleRequest,
): Promise<TYPE.WorkflowSchedule> => {
  const payload: Record<string, object | string | null> = {};
  if (data.cronExpression !== undefined) payload.cron_expression = data.cronExpression;
  if (data.timezone !== undefined) payload.timezone = data.timezone;
  if (data.initialInput !== undefined) payload.initial_input = data.initialInput;
  const response = await Request.put(API.updateWorkflowSchedule(id, scheduleId), payload);
  return normalizeWorkflowSchedule(response);
};

const toggleWorkflowSchedule = async (
  id: string,
  scheduleId: string,
  data: TYPE.ToggleWorkflowScheduleRequest,
): Promise<TYPE.WorkflowSchedule> => {
  const response = await Request.post(API.toggleWorkflowSchedule(id, scheduleId), data);
  return normalizeWorkflowSchedule(response);
};

const WORKFLOW = {
  getWorkflowsList,
  getWorkflowDetail,
  createWorkflow,
  updateWorkflow,
  deleteWorkflow,
  toggleWorkflowState,
  triggerWorkflowRun,
  getWorkflowRunsList,
  getWorkflowRunDetail,
  getWorkflowRunStatus,
  approveWorkflowRun,
  replayWorkflowRun,
  rerunWorkflowNode,
  getWorkflowSchedules,
  createWorkflowSchedule,
  updateWorkflowSchedule,
  toggleWorkflowSchedule,
};

export default WORKFLOW;
