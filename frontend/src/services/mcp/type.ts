export enum SERVER_CONNECTION {
  CONNECTED = 'connected',
  DISCONNECTED = 'disconnected',
  CONNECTING = 'connecting',
  ERROR = 'error',
}

export type SERVER_STATUS = {
  connection_state: SERVER_CONNECTION;
  requires_oauth: boolean;
  error?: string;
};

export type GET_SERVER_STATUS_RESPONSE = {
  success: boolean;
  connection_status: {
    [server_name: string]: SERVER_STATUS;
  };
};

export type GET_SERVER_STATUS_BY_ID_RESPONSE = {
  success: boolean;
  connection_state: SERVER_CONNECTION;
  requires_oauth: boolean;
};

export type GET_OAUTH_INITIATE_RESPONSE = {
  authorization_url: string;
  flow_id: string;
  server_name: string;
  user_id: string;
};

export type GET_SERVER_AUTH_URL_RESPONSE = {
  success: boolean;
  message: string;
  oauth_url: string;
  server_name: string;
  oauth_required: boolean;
};

export type CANCEL_AUTH_RESPONSE = {
  success: boolean;
  message: string;
};
