export enum ServerConnection {
  CONNECTED = 'connected',
  DISCONNECTED = 'disconnected',
  CONNECTING = 'connecting',
  ERROR = 'error',
}

export type ServerStatus = {
  connectionState: ServerConnection;
  requiresOauth: boolean;
  error?: string;
};

export type GetServerStatusByIdResponse = {
  success: boolean;
  connectionState: ServerConnection;
  requiresOauth: boolean;
};

export type GetOauthInitiateResponse = {
  authorizationUrl: string;
  flowId: string;
  serverName: string;
  userId: string;
};

export type GetServerAuthUrlResponse = {
  success: boolean;
  message: string;
  oauthUrl: string;
  serverName: string;
  oauthRequired: boolean;
};

export type CancelAuthResponse = {
  success: boolean;
  message: string;
};
