export type GET_AUTH_ME_RESPONSE = {
  username: string;
  email: string;
  scopes: string[];
  groups: string[];
  auth_method: string;
  provider: string;
  can_modify_servers: boolean;
  is_admin: boolean;
};

export type GET_TOKEN_REQUEST = {
  expires_in_hours: number;
  description: string;
  scope_method?: 'current' | 'custom';
  custom_scopes?: string;
};

type TOKEN_DATA = {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  refresh_expires_in: number;
  scope: string;
  issued_at: number;
  description: string;
};

export type GET_TOKEN_RESPONSE = {
  success: boolean;
  tokens: TOKEN_DATA & { token_type: string };
  keycloak_url: string;
  realm: string;
  client_id: string;
  token_data: TOKEN_DATA;
  user_scopes: string[];
  requested_scopes: string[];
};
