import type { AxiosRequestConfig } from 'axios';

import API from '@/services/api';
import Request from '@/services/request';

import type * as TYPE from './type';

const getVersion: () => Promise<TYPE.GetVersionResponse> = async () => await Request.get(API.getVersion);

const getServers: (data?: TYPE.GetServersRequest) => Promise<TYPE.GetServersResponse> = async data =>
  await Request.get(API.getServers, data);

const getServerDetail: (id: string) => Promise<TYPE.GetServersDetailResponse> = async id =>
  await Request.get(API.getServerDetail(id));

const testServerUrl: (
  data: TYPE.TestServerUrlRequest,
  config?: AxiosRequestConfig,
) => Promise<TYPE.TestServerUrlResponse> = async (data, config) =>
  await Request.post(API.testServerUrl, data, config);

const createServer: (data: TYPE.CreateServerRequest) => Promise<TYPE.Server> = async data =>
  await Request.post(API.createServer, data);

const updateServer: (id: string, data: Partial<TYPE.Server>) => Promise<TYPE.Server> = async (id, data) =>
  await Request.patch(API.updateServer(id), data);

const deleteServer: (id: string) => Promise<void> = async id => await Request.delete(API.deleteServer(id));

const toggleServerStatus: (id: string, data: { enabled: boolean }) => Promise<void> = async (id, data) =>
  await Request.post(API.toggleServerStatus(id), data);

const getServerTools: (id: string) => Promise<TYPE.GetServerToolsResponse> = async id =>
  await Request.get(API.getServerTools(id));

const refreshServerHealth: any = async (id: string) => await Request.post(API.refreshServerHealth(id));

export default {
  getVersion,
  getServers,
  getServerDetail,
  testServerUrl,
  createServer,
  updateServer,
  deleteServer,
  toggleServerStatus,
  getServerTools,
  refreshServerHealth,
};
