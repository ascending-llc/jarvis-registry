import API from '@/services/api';
import Request from '@/services/request';

import type * as TOKEN_TYPE from './type';

const logout: () => Promise<void> = async () => await Request.post(API.logout);

const refreshToken: () => Promise<any> = async () => await Request.post(API.refreshToken);

const getAuthProviders: () => Promise<any> = async () => await Request.get(API.getAuthProviders);

const getAuthMe: () => Promise<TOKEN_TYPE.GetAuthMeResponse> = async () => await Request.get(API.getAuthMe);

const getToken: (data: TOKEN_TYPE.GetTokenRequest) => Promise<TOKEN_TYPE.GetTokenResponse> = async data =>
  await Request.post(API.getToken, data, { skipTokenBarrier: true });

export default {
  logout,
  refreshToken,
  getAuthProviders,
  getAuthMe,
  getToken,
};
