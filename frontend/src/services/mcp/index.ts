import API from '@/services/api';
import Request from '@/services/request';

import type * as TYPE from './type';

const getServerStatusById: (id: string) => Promise<TYPE.GET_SERVER_STATUS_BY_ID_RESPONSE> = async id =>
  await Request.get(API.getServerStatusById(id));

const getOauthInitiate: (id: string) => Promise<TYPE.GET_OAUTH_INITIATE_RESPONSE> = async id =>
  await Request.get(API.getOauthInitiate(id));

const getOauthReinit: (id: string) => Promise<TYPE.GET_SERVER_AUTH_URL_RESPONSE> = async id =>
  await Request.post(API.getOauthReinit(id));

const cancelAuth: (id: string) => Promise<TYPE.CANCEL_AUTH_RESPONSE> = async id =>
  await Request.post(API.cancelAuth(id));

const revokeAuth: (id: string) => Promise<TYPE.CANCEL_AUTH_RESPONSE> = async id =>
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
