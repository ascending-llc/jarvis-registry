import API from '@/services/api';
import Request from '@/services/request';

import type * as TYPE from './type';

const getServerStatusById: (id: string) => Promise<TYPE.GetServerStatusByIdResponse> = async id =>
  await Request.get(API.getServerStatusById(id));

const getOauthInitiate: (id: string) => Promise<TYPE.GetOauthInitiateResponse> = async id =>
  await Request.get(API.getOauthInitiate(id));

const getOauthReinit: (id: string) => Promise<TYPE.GetServerAuthUrlResponse> = async id =>
  await Request.post(API.getOauthReinit(id));

const cancelAuth: (id: string) => Promise<TYPE.CancelAuthResponse> = async id =>
  await Request.post(API.cancelAuth(id));

const revokeAuth: (id: string) => Promise<TYPE.CancelAuthResponse> = async id =>
  await Request.delete(API.revokeAuth(id));

const getDiscover: (url: string, config?: object) => Promise<any> = async (url, config) =>
  await Request.get(API.getDiscover, { url }, config);

export default {
  getServerStatusById,
  getOauthInitiate,
  getOauthReinit,
  cancelAuth,
  revokeAuth,
  getDiscover,
};
