export enum SERVER_CONNECTION {
  CONNECTED = 'connected',
  DISCONNECTED = 'disconnected',
  CONNECTING = 'connecting',
  ERROR = 'error',
}

export type SERVER_STATUS = {
  connectionState: SERVER_CONNECTION;
  requiresOauth: boolean;
  error?: string;
};

export type GET_SERVER_STATUS_BY_ID_RESPONSE = {
  success: boolean;
  connectionState: SERVER_CONNECTION;
  requiresOauth: boolean;
};

export type GET_OAUTH_INITIATE_RESPONSE = {
  authorizationUrl: string;
  flowId: string;
  serverName: string;
  userId: string;
};

export type GET_SERVER_AUTH_URL_RESPONSE = {
  success: boolean;
  message: string;
  oauthUrl: string;
  serverName: string;
  oauthRequired: boolean;
};

export type CANCEL_AUTH_RESPONSE = {
  success: boolean;
  message: string;
};
