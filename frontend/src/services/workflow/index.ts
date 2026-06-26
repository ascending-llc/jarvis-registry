import API from '@/services/api';
import Request from '@/services/request';

import type * as TYPE from './type';

const getWorkflowsList = async (data?: TYPE.GetWorkflowsListRequest): Promise<TYPE.GetWorkflowsListResponse> =>
  await Request.get(API.getWorkflowsList, data);

const getWorkflowDetail = async (id: string): Promise<TYPE.GetWorkflowDetailResponse> =>
  await Request.get(API.getWorkflowDetail(id));

const createWorkflow = async (data: TYPE.CreateWorkflowRequest): Promise<TYPE.CreateWorkflowResponse> =>
  await Request.post(API.createWorkflow, data);

const updateWorkflow = async (id: string, data: TYPE.UpdateWorkflowRequest): Promise<TYPE.UpdateWorkflowResponse> =>
  await Request.put(API.updateWorkflow(id), data);

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
): Promise<TYPE.GetWorkflowRunsListResponse> => await Request.get(API.getWorkflowRunsList(id), data);

const getWorkflowRunDetail = async (id: string, runId: string): Promise<TYPE.GetWorkflowRunDetailResponse> =>
  await Request.get(API.getWorkflowRunDetail(id, runId));

const replayWorkflowRun = async (id: string, runId: string): Promise<TYPE.ReplayWorkflowRunResponse> =>
  await Request.post(API.replayWorkflowRun(id, runId));

const rerunWorkflowNode = async (id: string, runId: string, nodeId: string): Promise<TYPE.RerunWorkflowNodeResponse> =>
  await Request.post(API.rerunWorkflowNode(id, runId, nodeId));

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
  replayWorkflowRun,
  rerunWorkflowNode,
};

export default WORKFLOW;
