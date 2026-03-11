import axios, { type AxiosError, type AxiosRequestConfig } from 'axios';

import { getBasePath } from '@/config';
import API from '@/services/api';
import type { GetTokenResponse } from './auth/type';

const cancelSources: Record<string, () => void> = {};
const service = axios.create({ baseURL: getBasePath() || '/', timeout: 20000 });

type RequestConfig = AxiosRequestConfig & {
  cancelTokenKey?: string;
  skipTokenBarrier?: boolean;
  __isRetry?: boolean;
  __isRefresh?: boolean;
};

let tokenInitPromise: Promise<GetTokenResponse> | null = null;
let refreshTokenPromise: Promise<void> | null = null;

export const setTokenInitPromise = (promise: Promise<GetTokenResponse> | null) => {
  tokenInitPromise = promise;
};

service.interceptors.request.use(
  async (config: any) => {
    if (!config.skipTokenBarrier && tokenInitPromise) {
      await tokenInitPromise;
    }

    if (config.cancelTokenKey) {
      config.cancelToken = new axios.CancelToken(cancel => {
        cancelSources[config.cancelTokenKey || ''] = cancel;
      });
    }

    config.headers = config.headers || {};
    config.headers['Content-Type'] = 'application/json; charset=utf-8';

    return config;
  },
  error => {
    return Promise.reject(error);
  },
);

service.interceptors.response.use(
  response => {
    const key = (response?.config as RequestConfig)?.cancelTokenKey || '';
    if (key && cancelSources[key]) delete cancelSources[key];
    return response;
  },
  (error: AxiosError) => {
    if (axios.isCancel(error)) {
      return Promise.resolve({
        data: { Code: -200, message: 'Cancel request', cause: 'Cancel request' },
      } as any);
    }
    const status = error.response?.status;
    const originalConfig = (error.config || {}) as RequestConfig;
    if (status === 401 && !originalConfig.__isRetry && !originalConfig.__isRefresh) {
      if (!refreshTokenPromise) {
        refreshTokenPromise = service
          .post(API.refreshToken, undefined, { skipTokenBarrier: true, __isRefresh: true } as RequestConfig)
          .then(() => undefined)
          .catch(async () => {
            const isOnLoginPage =
              typeof window !== 'undefined' && window.location.pathname === `${getBasePath()}/login`;
            if (!isOnLoginPage) {
              try {
                await service.post(API.logout, undefined, {
                  skipTokenBarrier: true,
                  __isRefresh: true,
                } as RequestConfig);
              } catch (_error) {}
              if (typeof window !== 'undefined') {
                window.location.href = `${getBasePath()}/login`;
              }
            }
            throw {
              __refreshError: true,
              originalData: (error as AxiosError).response?.data,
            };
          })
          .finally(() => {
            refreshTokenPromise = null;
          });
      }
      return refreshTokenPromise.then(() =>
        service({
          ...(originalConfig || {}),
          __isRetry: true,
        } as RequestConfig),
      );
    }
    return Promise.reject(error);
  },
);

type RequestType = { url: string; method: string; data?: object; config?: AxiosRequestConfig; reTry?: boolean };
const request = async ({ url, method, data = {}, config = {} }: RequestType) => {
  const body = method.toUpperCase() === 'GET' ? { params: data } : { data };
  try {
    const response = await service({ url, method, ...body, ...config });
    return response?.data;
  } catch (error) {
    if (error && typeof error === 'object' && '__refreshError' in error) {
      throw (error as { originalData?: unknown }).originalData;
    }
    throw (error as AxiosError).response?.data;
  }
};

const requestGet = (url: string, data?: object, config = {}) => request({ url, data, config, method: 'GET' });
const requestPut = (url: string, data?: object, config = {}) => request({ url, data, config, method: 'PUT' });
const requestPost = (url: string, data?: object, config = {}) => request({ url, data, config, method: 'POST' });
const requestPatch = (url: string, data?: object, config = {}) => request({ url, data, config, method: 'PATCH' });
const requestDelete = (url: string, data?: object, config = {}) => request({ url, data, config, method: 'DELETE' });

const Request = {
  get: requestGet,
  put: requestPut,
  post: requestPost,
  patch: requestPatch,
  delete: requestDelete,
  cancels: cancelSources,
};

export default Request;
