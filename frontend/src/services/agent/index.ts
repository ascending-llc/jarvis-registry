import type { AxiosRequestConfig } from 'axios';

import API from '@/services/api';
import Request from '@/services/request';

import type * as TYPE from './type';

const getAgentsList: (data: TYPE.GetAgentsListRequest) => Promise<TYPE.GetAgentsListResponse> = async data =>
  await Request.get(API.getAgentsList, data);

const getAgentState: () => Promise<TYPE.GetAgentStateResponse> = async () => await Request.get(API.getAgentState);

const getAgentDetail: (id: string) => Promise<TYPE.GetAgentDetailResponse> = async id =>
  await Request.get(API.getAgentDetail(id));

const createAgent: (data: TYPE.CreateAgentRequest) => Promise<TYPE.Agent> = async data =>
  await Request.post(API.createAgent, data);

const updateAgent: (id: string, data: TYPE.UpdateAgentRequest) => Promise<TYPE.Agent> = async (id, data) =>
  await Request.patch(API.updateAgent(id), data);

const deleteAgent: (id: string) => Promise<void> = async id => await Request.delete(API.deleteAgent(id));

const toggleAgentState: (id: string, data: TYPE.ToggleAgentStateRequest) => Promise<TYPE.Agent> = async (id, data) =>
  await Request.post(API.toggleAgentState(id), data);

const testAgentUrl: (url: string, config: any) => Promise<any> = async () => ({ success: true });

const getAgentSkills: (id: string) => Promise<TYPE.GetAgentSkillsResponse> = async id =>
  await Request.get(API.getAgentSkills(id));

const getWellKnownAgentCards: (
  url: string,
  config?: AxiosRequestConfig,
) => Promise<TYPE.GetWellKnownAgentCardsResponse> = async (url, config) =>
  await Request.get(API.getWellKnownAgentCards, { url }, config);

const AGENT = {
  getAgentsList,
  getAgentState,
  getAgentDetail,
  createAgent,
  updateAgent,
  deleteAgent,
  toggleAgentState,
  testAgentUrl,
  getAgentSkills,
  getWellKnownAgentCards,
};

export default AGENT;
